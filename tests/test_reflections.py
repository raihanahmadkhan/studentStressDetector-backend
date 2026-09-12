import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4
import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select, func, update

from app import reflections, llm_provider
from app.config import Settings
from app.llm_provider import Selection
from app.models import AIRequest, Explanation, User, utcnow
from app.reflection_facts import render
from test_checkins import save, signed_in, identity


@pytest.fixture(autouse=True)
def historical_reflection_engine(client):
    """Retired engine regression coverage; production does not mount these routes."""
    client.app.include_router(reflections.router)


@pytest.fixture
def provider(monkeypatch):
    settings = Settings(_env_file=None, app_env='test', llm_enabled=True, openai_api_key='test-not-a-real-key', llm_model='test-model')
    monkeypatch.setattr(reflections, 'get_settings', lambda: settings)
    calls = []
    async def valid(facts, config):
        calls.append(facts)
        return Selection.model_validate({'highlights': [{'evidence_id': 'F01', 'question_id': facts['facts'][0]['question_ids'][0]}]}), {'total_tokens': 20}
    monkeypatch.setattr(reflections, 'select_highlights', valid)
    return calls


def prepare(client):
    saved = save(client).json()
    consent = client.patch('/api/ai/consent', json={'enabled': True})
    assert consent.status_code == 200
    return {'kind': 'checkin', 'checkin_id': saved['id'], 'expected_history_version': saved['history_version']}, saved


def request_reflection(client, data, key=None):
    return client.post('/api/ai/reflections', json=data, headers={'Idempotency-Key': str(key or uuid4())})


def test_unconfigured_or_no_consent_never_calls_provider(signed_in, monkeypatch):
    client, _ = signed_in
    data, _ = prepare(client)
    async def forbidden(*args):
        pytest.fail('Provider must not be contacted')
    monkeypatch.setattr(reflections, 'select_highlights', forbidden)
    assert request_reflection(client, data).json()['reason'] == 'provider_unconfigured'
    client.patch('/api/ai/consent', json={'enabled': False})
    settings = Settings(_env_file=None, app_env='test', llm_enabled=True, openai_api_key='fake', llm_model='test-model')
    monkeypatch.setattr(reflections, 'get_settings', lambda: settings)
    assert request_reflection(client, data).json()['reason'] == 'consent_required'


def test_grounded_evidence_provenance_cache_and_export(signed_in, provider, db):
    client, user = signed_in
    data, saved = prepare(client)
    key = uuid4()
    first = request_reflection(client, data, key)
    assert first.status_code == 200, first.text
    value = first.json()
    assert value['status'] == 'grounded'
    assert value['output']['highlights'][0]['evidence_id'] == 'F01'
    assert '6 out of 10' in value['output']['highlights'][0]['text']
    assert request_reflection(client, data, key).json()['id'] == value['id']
    assert request_reflection(client, data).json()['id'] == value['id']
    assert len(provider) == 1
    prompt = json.dumps(provider[0])
    assert str(user.id) not in prompt and saved['id'] not in prompt and 'csrf' not in prompt
    exported = client.get('/api/data/export').json()
    assert exported['reflections'][0]['source_hash'] == value['source_hash']
    assert db.scalar(select(func.count()).select_from(AIRequest)) == 1


@pytest.mark.parametrize('selection', [
    {'highlights': [{'evidence_id': 'F99', 'question_id': 'routine'}]},
    {'highlights': [{'evidence_id': 'F01', 'question_id': 'sleep'}]},
    {'highlights': [{'evidence_id': 'F01', 'question_id': 'strain'}]*2},
    {'highlights': [{'evidence_id': 'F01', 'question_id': 'strain', 'text': 'You have a disorder'}]},
    {'highlights': [], 'score': 99},
    {'highlights': [{'evidence_id': '<script>', 'question_id': 'routine'}]},
    {'highlights': [{'evidence_id': 'F01', 'question_id': 'strain'}]*4},
])
def test_hallucinated_or_malformed_output_falls_back(signed_in, provider, monkeypatch, selection):
    client, _ = signed_in
    data, _ = prepare(client)
    async def invalid(*args):
        return Selection.model_validate(selection), {}
    monkeypatch.setattr(reflections, 'select_highlights', invalid)
    body = request_reflection(client, data).json()
    assert body['status'] == 'fallback'
    assert body['reason'] == 'provider_unavailable_or_invalid'
    assert all(h['text'] == next(f['text'] for f in body['evidence'] if f['id'] == h['evidence_id']) for h in body['output']['highlights'])
    assert '<script>' not in json.dumps(body) and 'You have a disorder' not in json.dumps(body)


def test_quota_survives_history_invalidation(signed_in, provider, db):
    client, user = signed_in
    data, saved = prepare(client)
    for i in range(5):
        db.add(AIRequest(user_id=user.id, request_key=uuid4(), payload_hash='a'*64, source_hash='b'*64, status='fallback', created_at=utcnow()-timedelta(minutes=2)))
    db.commit()
    assert request_reflection(client, data).json()['reason'] == 'rate_limited'
    updated = client.patch('/api/check-ins/'+saved['id'], json={**saved['inputs'], 'expected_revision': 1}, headers={'Idempotency-Key': str(uuid4())}).json()
    data['expected_history_version'] = updated['history_version']
    assert request_reflection(client, data).json()['reason'] == 'rate_limited'
    assert provider == []


@pytest.mark.parametrize('mutation', ['edit', 'revoke', 'revoke_reenable', 'logout', 'delete'])
def test_source_or_consent_changes_during_generation_are_discarded(signed_in, provider, monkeypatch, test_engine, mutation):
    client, _ = signed_in
    data, saved = prepare(client)
    entered, release = Event(), Event()
    async def waiting(facts, settings):
        entered.set()
        assert release.wait(5)
        return Selection.model_validate({'highlights': [{'evidence_id': 'F01', 'question_id': 'strain'}]}), {}
    monkeypatch.setattr(reflections, 'select_highlights', waiting)
    def generate():
        with TestClient(client.app, base_url='http://localhost', headers=dict(client.headers), cookies=dict(client.cookies)) as concurrent:
            return request_reflection(concurrent, data)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generate)
        assert entered.wait(5)
        try:
            if mutation == 'edit':
                assert client.patch('/api/check-ins/'+saved['id'], json={**saved['inputs'], 'expected_revision': 1, 'sleep_hours': 8}, headers={'Idempotency-Key': str(uuid4())}).status_code == 200
            elif mutation.startswith('revoke'):
                assert client.patch('/api/ai/consent', json={'enabled': False}).status_code == 200
                if mutation == 'revoke_reenable':
                    client.patch('/api/ai/consent', json={'enabled': True})
            elif mutation == 'logout':
                client.post('/api/auth/logout')
            else:
                client.request('DELETE', '/api/check-ins/'+saved['id'], json={'expected_revision': 1}, headers={'Idempotency-Key': str(uuid4())})
        finally:
            release.set()
        assert future.result().status_code == (401 if mutation == 'logout' else 409)
    from sqlalchemy.orm import Session
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(Explanation)) == 0


def test_user_isolation_client_facts_rejected_weekly_missingness(signed_in, provider, db):
    client, _ = signed_in
    data, saved = prepare(client)
    assert request_reflection(client, {**data, 'facts': [{'score': 99}]}).status_code == 422
    _, token, csrf = identity(db)
    client.cookies.set('wellbeing_session', token); client.headers['X-CSRF-Token'] = csrf
    data['expected_history_version'] = 0
    assert request_reflection(client, data).status_code == 404
    weekly = request_reflection(client, {'kind': 'weekly', 'end_date': saved['observation_date'], 'expected_history_version': 0}).json()
    assert 'not enough data' in weekly['output']['highlights'][0]['text']


def test_revocation_deletes_reflections_but_keeps_quota(signed_in, provider, db):
    client, _ = signed_in
    data, _ = prepare(client)
    request_reflection(client, data)
    assert client.patch('/api/ai/consent', json={'enabled': False}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Explanation)) == 0
    assert db.scalar(select(func.count()).select_from(AIRequest)) == 1


@pytest.mark.parametrize('response', [
    {'status': 'incomplete', 'output': []},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}]},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{broken'}]}]},
])
def test_provider_wire_errors_and_refusal(response, monkeypatch):
    original = httpx.AsyncClient
    def transport(request):
        body = json.loads(request.content)
        assert body['store'] is False and body['text']['format']['strict'] is True
        assert 'tools' not in body and body['max_output_tokens'] == 600
        return httpx.Response(200, json=response)
    monkeypatch.setattr(llm_provider.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    settings = Settings(_env_file=None, openai_api_key='fake', llm_model='test-model')
    with pytest.raises((ValueError, KeyError)):
        asyncio.run(llm_provider.select_highlights({'facts': []}, settings))


def test_provider_outer_deadline(monkeypatch):
    original = httpx.AsyncClient
    async def transport(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json={})
    monkeypatch.setattr(llm_provider.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    settings = Settings(_env_file=None, openai_api_key='fake', llm_model='test-model', llm_timeout_seconds=1)
    with pytest.raises(TimeoutError):
        asyncio.run(llm_provider.select_highlights({'facts': []}, settings))


def test_provider_successful_wire_contract_and_token_metadata(monkeypatch):
    original = httpx.AsyncClient
    def transport(request):
        assert str(request.url) == 'https://api.openai.com/v1/responses'
        body = json.loads(request.content)
        assert body['model'] == 'test-model' and body['store'] is False
        assert body['text']['format']['schema']['additionalProperties'] is False
        assert body['max_output_tokens'] == 600 and 'tools' not in body
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"highlights":[{"evidence_id":"F01","question_id":"routine"}]}'}]}], 'usage': {'input_tokens': 200, 'output_tokens': 20, 'total_tokens': 220, 'private_data': 'ignored'}})
    monkeypatch.setattr(llm_provider.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    selection, usage = asyncio.run(llm_provider.select_highlights({'facts': []}, Settings(_env_file=None, llm_model='test-model', openai_api_key='fake')))
    assert selection.highlights[0].evidence_id == 'F01'
    assert usage == {'input_tokens': 200, 'output_tokens': 20, 'total_tokens': 220}


@pytest.mark.parametrize('status_code', [429, 500, 302])
def test_provider_http_failures_are_not_retried_or_redirected(monkeypatch, status_code):
    original = httpx.AsyncClient
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status_code, headers={'Location': 'https://untrusted.invalid'}, text='provider private error')
    monkeypatch.setattr(llm_provider.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(llm_provider.select_highlights({'facts': []}, Settings(_env_file=None, llm_model='test-model', openai_api_key='fake')))
    assert len(calls) == 1


def test_large_provider_response_rejected(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(llm_provider.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x'*32769)), **kwargs))
    with pytest.raises(ValueError, match='exceeds budget'):
        asyncio.run(llm_provider.select_highlights({'facts': []}, Settings(_env_file=None, llm_model='test-model', openai_api_key='fake')))


def test_duplicate_inflight_request_reserves_only_once(signed_in, provider, monkeypatch, db):
    client, _ = signed_in
    data, _ = prepare(client)
    key = uuid4()
    entered, release = Event(), Event()
    calls = []
    async def waiting(facts, settings):
        calls.append(1); entered.set()
        assert release.wait(5)
        return Selection.model_validate({'highlights': [{'evidence_id': 'F01', 'question_id': 'strain'}]}), {}
    monkeypatch.setattr(reflections, 'select_highlights', waiting)
    def generate():
        with TestClient(client.app, base_url='http://localhost', headers=dict(client.headers), cookies=dict(client.cookies)) as concurrent:
            return request_reflection(concurrent, data, key)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generate)
        assert entered.wait(5)
        try:
            assert request_reflection(client, data, key).json()['reason'] == 'request_already_reserved'
            assert request_reflection(client, data).json()['reason'] == 'rate_limited'
        finally:
            release.set()
        assert future.result().json()['status'] == 'grounded'
    assert len(calls) == 1 and db.scalar(select(func.count()).select_from(AIRequest)) == 1

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
import pytest

from app import fuzzy
from app.models import CheckIn, CheckInRevision, EngineVersion, Session, User, utcnow

ORIGIN = 'http://localhost:5173'


def identity(db):
    user = User(oidc_issuer='test', oidc_subject=str(uuid4()))
    db.add(user)
    db.flush()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(Session(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), csrf_token=csrf,
                   expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    return user, token, csrf


@pytest.fixture
def signed_in(client, db):
    user, token, csrf = identity(db)
    client.cookies.set('wellbeing_session', token)
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': csrf})
    return client, user


def payload(**changes):
    return dict(observation_date=(date.today()-timedelta(days=1)).isoformat(), timezone='Asia/Kolkata',
                sleep_hours=7, academic_load=5, screen_hours=6, extracurricular_load=5,
                deadline_pressure=5, recovery=5, reported_strain=6, **changes)


def save(client, data=None, key=None):
    return client.post('/api/check-ins', json=data or payload(), headers={'Idempotency-Key': str(key or uuid4())})


def test_save_and_retry_return_exact_committed_snapshot(signed_in, db):
    client, user = signed_in
    assert client.get('/api/check-ins').json()['items'] == []
    key = uuid4()
    first = save(client, key=key)
    assert first.status_code == 201, first.text
    result = first.json()
    assert result['assessment']['status'] == 'ok'
    assert result['assessment']['score'] == fuzzy.evaluate(result['inputs'])['score']
    assert result['inputs']['reported_strain'] == 6
    assert result['assessment'] == fuzzy.evaluate(result['inputs'])
    assert 1 <= len(result['guidance']) <= 3
    assert all(item['policy_version'] == 'guidance-1.0.0' for item in result['guidance'])
    second = save(client, key=key)
    assert second.status_code == 200 and second.json() == result
    assert second.headers['Idempotency-Replayed'] == 'true'
    assert db.scalar(select(func.count()).select_from(CheckIn)) == 1
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 1
    db.refresh(user)
    assert user.history_version == 1
    assert client.get('/api/check-ins/' + result['id']).json() == result


def test_payload_key_reuse_conflicts_but_second_daily_save_updates(signed_in):
    client, _ = signed_in
    key = uuid4()
    assert save(client, key=key).status_code == 201
    changed = payload()
    changed['sleep_hours'] = 8
    assert save(client, changed, key).json()['error']['code'] == 'idempotency_conflict'
    updated = save(client, changed)
    assert updated.status_code == 200
    assert updated.json()['revision'] == 2
    assert updated.json()['inputs']['sleep_hours'] == 8


def test_daily_overwrite_preserves_revisions_and_retry_does_not_revert_latest(signed_in, db):
    client, user = signed_in
    first = save(client).json()
    changed = {**payload(), 'sleep_hours': 9, 'academic_load': 9}
    key = uuid4()
    second = save(client, changed, key).json()
    third = save(client, {**changed, 'sleep_hours': 4}).json()
    assert first['id'] == second['id'] == third['id']
    assert third['revision'] == 3
    assert save(client, changed, key).json() == second
    assert client.get('/api/check-ins/' + first['id']).json() == third
    assert client.get('/api/check-ins').json()['items'] == [third]
    assert db.scalar(select(func.count()).select_from(CheckIn)) == 1
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 3
    assert third['assessment'] == fuzzy.evaluate(third['inputs'])
    db.refresh(user)
    assert user.history_version == 3


def test_edit_keeps_old_revision_and_rejects_stale_write(signed_in, db):
    client, user = signed_in
    create_key = uuid4()
    first = save(client, key=create_key).json()
    edit = {**first['inputs'], 'sleep_hours': 9, 'expected_revision': 1}
    key = str(uuid4())
    url = '/api/check-ins/' + first['id']
    updated = client.patch(url, json=edit, headers={'Idempotency-Key': key})
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2
    assert updated.json()['history_version'] == 2
    assert client.patch(url, json=edit, headers={'Idempotency-Key': key}).json() == updated.json()
    assert client.patch(url, json=edit, headers={'Idempotency-Key': str(uuid4())}).status_code == 409
    assert save(client, key=create_key).json() == first
    assert client.get(url).json()['revision'] == 2
    assert len(db.scalars(select(CheckInRevision)).all()) == 2
    db.refresh(user)
    assert user.history_version == 2


def test_cross_user_reads_and_writes_are_isolated(signed_in, db):
    client, _ = signed_in
    first = save(client).json()
    _, token, csrf = identity(db)
    client.cookies.set('wellbeing_session', token)
    client.headers['X-CSRF-Token'] = csrf
    url = '/api/check-ins/' + first['id']
    assert client.get(url).status_code == 404
    assert client.get('/api/check-ins').json()['items'] == []
    assert client.patch(url, json={**first['inputs'], 'expected_revision': 1}, headers={'Idempotency-Key': str(uuid4())}).status_code == 404
    assert save(client).status_code == 201


@pytest.mark.parametrize('field,value', [('sleep_hours', True), ('sleep_hours', '7'), ('sleep_hours', 7.1), ('sleep_hours', -1),
    ('screen_hours', 25), ('academic_load', 5.5), ('academic_load', True), ('reported_strain', 11),
    ('timezone', 'invalid/zone'), ('observation_date', 'tomorrow'), ('user_id', str(uuid4()))])
def test_external_input_rejected(signed_in, field, value):
    client, _ = signed_in
    data = payload()
    data[field] = value
    response = save(client, data)
    assert response.status_code == 422, response.text
    assert client.get('/api/check-ins').json()['items'] == []


def test_future_date_is_rejected(signed_in):
    client, _ = signed_in
    data = payload()
    data['observation_date'] = (date.today() + timedelta(days=3)).isoformat()
    assert save(client, data).status_code == 422


def test_new_questionnaire_rejects_out_of_range_hours(signed_in):
    client, _ = signed_in
    data = payload()
    data.update(sleep_hours=13.25, reported_strain=None)
    assert save(client, data).status_code == 422
    assert client.get('/api/check-ins').json()['items'] == []


def test_unexpected_fuzzy_failure_saves_observation_with_error(signed_in, monkeypatch):
    client, _ = signed_in
    def fail(_):
        raise RuntimeError('test')
    monkeypatch.setattr(fuzzy, 'evaluate', fail)
    result = save(client)
    assert result.status_code == 201
    assert result.json()['assessment']['status'] == 'error'
    assert result.json()['assessment']['score'] is None


def test_model_version_conflict_rolls_back(signed_in, db):
    client, _ = signed_in
    db.add(EngineVersion(version=fuzzy.MODEL_VERSION, kind='fuzzy', spec_hash='0'*64, specification={}))
    db.commit()
    assert save(client).status_code == 503
    assert db.scalar(select(func.count()).select_from(CheckIn)) == 0


def test_concurrent_same_key_creates_only_one_observation(signed_in, db):
    original, _ = signed_in
    key = uuid4()
    def request(_):
        with TestClient(original.app, base_url='http://localhost') as client:
            client.cookies.update(original.cookies)
            client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': original.headers['X-CSRF-Token']})
            response = save(client, key=key)
            return response.status_code, response.json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, range(4)))
    assert sorted(status for status, _ in results) == [200, 200, 200, 201]
    assert all(result == results[0][1] for _, result in results)
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 1


def test_concurrent_daily_saves_keep_last_committed_revision(signed_in, db):
    original, _ = signed_in
    def request(hours):
        with TestClient(original.app, base_url='http://localhost') as client:
            client.cookies.update(original.cookies)
            client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': original.headers['X-CSRF-Token']})
            response = save(client, {**payload(), 'sleep_hours': hours})
            assert response.status_code in (200, 201)
            return response.json()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(request, [4, 7, 9]))
    assert sorted(item['revision'] for item in results) == [1, 2, 3]
    latest = max(results, key=lambda item: item['revision'])
    assert original.get('/api/check-ins').json()['items'] == [latest]
    assert db.scalar(select(func.count()).select_from(CheckIn)) == 1
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 3


def test_database_checks_prevent_invalid_rating(signed_in, db):
    client, _ = signed_in
    assert save(client).status_code == 201
    with pytest.raises(IntegrityError):
        db.execute(text('UPDATE checkin_revisions SET academic_load = 11'))
        db.commit()
    db.rollback()


def test_concurrent_edits_have_one_winner(signed_in, db):
    original, user = signed_in
    first = save(original).json()
    def edit(hours):
        with TestClient(original.app, base_url='http://localhost') as client:
            client.cookies.update(original.cookies)
            response = client.patch('/api/check-ins/' + first['id'],
                json={**first['inputs'], 'expected_revision': 1, 'sleep_hours': hours},
                headers={'Origin': ORIGIN, 'X-CSRF-Token': original.headers['X-CSRF-Token'], 'Idempotency-Key': str(uuid4())})
            return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(edit, [8, 9]))
    assert sorted(statuses) == [200, 409]
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 2
    db.refresh(user)
    assert user.history_version == 2


def test_current_revision_must_exist_at_commit(signed_in, db):
    client, _ = signed_in
    assert save(client).status_code == 201
    db.execute(text('UPDATE checkins SET current_revision = 999'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_database_failure_is_sanitized(client):
    from app.database import get_db
    from sqlalchemy.exc import OperationalError
    def broken_db():
        raise OperationalError('secret SQL', {}, RuntimeError('private connection details'))
    client.app.dependency_overrides[get_db] = broken_db
    response = client.get('/api/health/ready')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'database_unavailable'
    assert 'secret SQL' not in response.text and 'private connection' not in response.text


def test_health_contract_and_no_unapproved_features(client):
    assert client.get('/api/health/live').status_code == 200
    ready = client.get('/api/health/ready')
    assert ready.status_code == 200, ready.text
    assert ready.json()['predictions_enabled'] is False
    schema = client.get('/openapi.json').json()
    assert '/api/check-ins' in schema['paths']
    assert '/api/scenarios' in schema['paths']
    assert '/api/explanations' not in schema['paths']
    assert client.post('/api/calculate-stress').status_code == 404

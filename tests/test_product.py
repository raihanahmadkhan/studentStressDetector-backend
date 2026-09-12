from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.analytics import summarize
from app.models import AIRequest, CheckIn, CheckInRevision, EngineVersion, Explanation, MutationReceipt, Session, User, utcnow
from test_checkins import identity, payload, save, signed_in


def delete_one(client, saved, revision=None, key=None):
    return client.request('DELETE', '/api/check-ins/'+saved['id'], json={'expected_revision': revision or saved['revision']}, headers={'Idempotency-Key': str(key or uuid4())})


def test_deletion_cascades_revisions_and_replays_without_resurrecting(signed_in, db):
    client, user = signed_in
    create_key = uuid4()
    saved = save(client, key=create_key).json()
    updated = client.patch('/api/check-ins/'+saved['id'], json={**saved['inputs'], 'expected_revision': 1, 'sleep_hours': 8}, headers={'Idempotency-Key': str(uuid4())}).json()
    assert delete_one(client, saved).status_code == 409
    key = uuid4()
    deleted = delete_one(client, updated, key=key)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {'deleted': True, 'history_version': 3}
    assert delete_one(client, updated, key=key).json() == deleted.json()
    assert save(client, key=create_key).status_code == 410
    assert client.get('/api/check-ins/'+saved['id']).status_code == 404
    assert client.get('/api/check-ins/'+saved['id']+'/revisions').status_code == 404
    assert db.scalar(select(func.count()).select_from(CheckInRevision)) == 0
    db.refresh(user)
    assert user.history_version == 3


def test_revisions_are_paged_immutable_and_snapshot_checked(signed_in):
    client, _ = signed_in
    saved = save(client).json()
    url = '/api/check-ins/'+saved['id']
    for n in range(1, 4):
        assert client.patch(url, json={**saved['inputs'], 'expected_revision': n, 'sleep_hours': 7+n/4}, headers={'Idempotency-Key': str(uuid4())}).status_code == 200
    page = client.get(url+'/revisions?limit=2').json()
    assert [r['revision'] for r in page['items']] == [4, 3]
    older = client.get(url+'/revisions', params={'before': page['next_cursor'], 'expected_history_version': 4}).json()
    assert [r['revision'] for r in older['items']] == [2, 1]
    assert older['items'][-1]['inputs'] == saved['inputs']
    assert client.get(url+'/revisions?expected_history_version=1').status_code == 409
    assert client.get('/api/check-ins?expected_history_version=1').status_code == 409


def test_scenarios_do_not_write_any_table_and_use_same_model(signed_in, db):
    client, user = signed_in
    def counts():
        return [db.scalar(select(func.count()).select_from(table)) for table in (User, Session, CheckIn, CheckInRevision, EngineVersion, Explanation, MutationReceipt, AIRequest)]
    before = counts()
    inputs = {k: v for k, v in payload().items() if k not in ('observation_date', 'timezone')}
    result = client.post('/api/scenarios', json={'inputs': inputs})
    assert result.status_code == 200, result.text
    assert result.json()['hypothetical'] is True
    assert result.json()['assessment']['status'] == 'ok'
    assert counts() == before
    saved = save(client).json()
    before = counts()
    compared = client.post('/api/scenarios', json={'inputs': {**inputs, 'sleep_hours': 4}, 'reference_id': saved['id']}).json()
    assert compared['reference']['revision'] == 1
    assert compared['reference']['assessment']['model_version'] == compared['assessment']['model_version']
    assert compared['difference'] == round(compared['assessment']['score']-compared['reference']['assessment']['score'], 1)
    assert counts() == before
    db.refresh(user)
    assert user.history_version == 1
    unsupported = client.post('/api/scenarios', json={'inputs': {**inputs, 'sleep_hours': 20}, 'reference_id': saved['id']})
    assert unsupported.status_code == 422


def test_other_users_cannot_read_export_edit_delete_or_reference(signed_in, db):
    client, first = signed_in
    saved = save(client).json()
    _, token, csrf = identity(db)
    client.cookies.set('wellbeing_session', token)
    client.headers['X-CSRF-Token'] = csrf
    assert delete_one(client, saved).status_code == 404
    assert client.get('/api/check-ins/'+saved['id']+'/revisions').status_code == 404
    assert client.post('/api/scenarios', json={'inputs': saved['inputs'], 'reference_id': saved['id']}).status_code == 404
    assert client.get('/api/data/export').json()['revisions'] == []
    assert all(m['recent_count'] == 0 for m in client.get('/api/patterns').json()['metrics'])
    assert db.get(CheckIn, saved['id']) is not None


def test_export_keeps_provenance_excludes_auth_secrets(signed_in):
    client, _ = signed_in
    saved = save(client).json()
    exported = client.get('/api/data/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['content-disposition']
    body = exported.json()
    assert body['revisions'][0] == saved
    assert body['models'][0]['spec_hash'] == saved['assessment']['spec_hash']
    for forbidden in ('csrf_token', 'token_hash', 'oidc_subject', 'mutation_key'):
        assert forbidden not in exported.text


def test_preferences_preserve_observation_date_and_timezone(signed_in):
    client, _ = signed_in
    saved = save(client).json()
    assert client.patch('/api/settings', json={'timezone': 'America/Los_Angeles'}).status_code == 200
    assert client.get('/api/me').json()['timezone'] == 'America/Los_Angeles'
    assert client.get('/api/check-ins/'+saved['id']).json() == saved
    assert client.patch('/api/settings', json={'timezone': 'invalid/timezone'}).status_code == 422


def test_delete_history_conflict_retry_and_late_create(signed_in, db):
    client, _ = signed_in
    key = uuid4()
    save(client, key=key)
    headers = {'Idempotency-Key': str(uuid4())}
    assert client.request('DELETE', '/api/data/history', json={'expected_history_version': 0, 'confirmation': 'DELETE'}, headers=headers).status_code == 409
    data = {'expected_history_version': 1, 'confirmation': 'DELETE'}
    deleted = client.request('DELETE', '/api/data/history', json=data, headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert client.request('DELETE', '/api/data/history', json=data, headers=headers).json() == deleted.json()
    assert save(client, key=key).status_code == 410
    assert db.scalar(select(func.count()).select_from(CheckIn)) == 0


def test_account_delete_requires_recent_login_and_revokes_all_sessions(signed_in, db):
    client, user = signed_in
    save(client)
    db.add(Session(user_id=user.id, token_hash=str(uuid4()).replace('-', '').ljust(64, '0'), csrf_token='other-session', expires_at=utcnow()+timedelta(hours=1)))
    db.commit()
    db.execute(update(Session).where(Session.user_id == user.id).values(created_at=utcnow()-timedelta(minutes=16)))
    db.commit()
    data = {'expected_history_version': 1, 'confirmation': 'DELETE'}
    assert client.request('DELETE', '/api/data/account', json=data).status_code == 403
    db.execute(update(Session).where(Session.user_id == user.id).values(created_at=utcnow()))
    db.commit()
    result = client.request('DELETE', '/api/data/account', json=data)
    assert result.status_code == 204, result.text
    assert client.get('/api/me').status_code == 401
    for table in (User, Session, CheckIn, CheckInRevision, MutationReceipt, Explanation, AIRequest):
        assert db.scalar(select(func.count()).select_from(table)) == 0


def test_edit_delete_race_has_no_lost_revision(signed_in, db):
    client, _ = signed_in
    saved = save(client).json()
    url = '/api/check-ins/'+saved['id']
    headers, cookies = dict(client.headers), dict(client.cookies)
    def operation(edit):
        with TestClient(client.app, base_url='http://localhost', headers=headers, cookies=cookies) as concurrent:
            if edit:
                return concurrent.patch(url, json={**saved['inputs'], 'expected_revision': 1, 'sleep_hours': 8}, headers={'Idempotency-Key': str(uuid4())}).status_code
            return delete_one(concurrent, saved).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(operation, [True, False]))
    assert statuses in ([200, 409], [404, 200])
    remaining = client.get(url)
    assert remaining.status_code == 404 or remaining.json()['revision'] == 2


@pytest.mark.parametrize('url,method,data', [('/api/scenarios', 'POST', {'inputs': {}}), ('/api/settings', 'PATCH', {'timezone': 'UTC'}), ('/api/data/history', 'DELETE', {'expected_history_version': 0, 'confirmation': 'DELETE'}), ('/api/data/account', 'DELETE', {'expected_history_version': 0, 'confirmation': 'DELETE'})])
def test_product_mutations_require_csrf(signed_in, url, method, data):
    client, _ = signed_in
    client.headers.pop('X-CSRF-Token')
    assert client.request(method, url, json=data, headers={'Idempotency-Key': str(uuid4())}).status_code == 403


END = date(2026, 9, 10)
def observation(days_ago, **changes):
    return {'observation_date': END-timedelta(days=days_ago), 'inputs': {'sleep_hours': 8, 'screen_hours': 6, 'academic_load': 5, 'extracurricular_load': 3, 'reported_strain': 4, **changes}}


def test_analytics_exact_count_thresholds_and_nullable_strain():
    rows = [observation(i) for i in range(7, 17)] + [observation(i, sleep_hours=6, reported_strain=None) for i in range(4)]
    result = summarize(rows, END)
    sleep = result['metrics'][0]
    assert sleep['difference'] == -2 and sleep['recent_count'] == 4 and sleep['baseline_count'] == 10
    assert sleep['threshold'] == 1 and sleep['persistent_deviation'] == 'lower'
    strain = result['metrics'][-1]
    assert strain['recent_count'] == 0 and strain['difference'] is None
    assert result['series'][-1]['rolling_mean']['reported_strain'] is None
    assert summarize(rows[:-1], END)['metrics'][0]['difference'] is None
    assert summarize(rows[1:], END)['metrics'][0]['difference'] is None


def test_missing_day_breaks_persistence_and_zero_is_real_data():
    rows = [observation(i) for i in range(7, 28)] + [observation(i, sleep_hours=0) for i in (0, 2, 3, 6)]
    result = summarize(rows, END)
    assert result['metrics'][0]['difference'] == -8
    assert result['metrics'][0]['latest_deviation'] == 'lower'
    assert result['metrics'][0]['persistent_deviation'] is None
    assert result['series'][-2]['inputs'] is None
    assert result['series'][-1]['rolling_mean']['sleep_hours'] == 0


def test_mad_robustness_window_edges_and_cooccurrence():
    rows = [observation(i, sleep_hours=8 if i % 2 else 9) for i in range(7, 28)]
    rows += [observation(i, sleep_hours=6, academic_load=9) for i in range(7)]
    rows += [observation(28, sleep_hours=0)]
    result = summarize(rows, END)
    assert result['metrics'][0]['baseline_count'] == 21
    assert result['metrics'][0]['recent_count'] == 7
    assert result['cooccurrence'] is not None
    assert len(result['series']) == 28
    assert result['windows']['baseline'] == ['2026-08-14', '2026-09-03']


def test_nonzero_mad_threshold_and_exact_deviation_boundary():
    rows = [observation(7+i, sleep_hours=5+i) for i in range(10)]
    rows += [observation(i, sleep_hours=19) for i in range(4)]
    metric = summarize(rows, END)['metrics'][0]
    assert metric['baseline_median'] == 9.5 and metric['mad'] == 2.5
    assert metric['threshold'] == pytest.approx(9.26625)
    assert metric['latest_deviation'] == 'higher'
    rows[-4] = observation(0, sleep_hours=18.75)
    assert summarize(rows, END)['metrics'][0]['latest_deviation'] is None
    constant = [observation(i) for i in range(7, 17)] + [observation(0, sleep_hours=7)]
    assert summarize(constant, END)['metrics'][0]['latest_deviation'] == 'lower'


def test_patterns_reads_latest_revisions_and_deletion(signed_in):
    client, _ = signed_in
    saved = save(client).json()
    updated = client.patch('/api/check-ins/'+saved['id'], json={**saved['inputs'], 'expected_revision': 1, 'sleep_hours': 9}, headers={'Idempotency-Key': str(uuid4())}).json()
    body = client.get('/api/patterns', params={'end_date': saved['observation_date']}).json()
    assert body['series'][-1]['inputs']['sleep_hours'] == 9
    assert body['metrics'][0]['recent_count'] == 1
    assert delete_one(client, updated).status_code == 200
    assert client.get('/api/patterns', params={'end_date': saved['observation_date']}).json()['series'][-1]['inputs'] is None

def test_export_contains_account_profile(signed_in, db):
    client, user = signed_in
    user.display_name = 'Export Student'
    user.google_email = 'export@example.com'
    db.commit()
    exported = client.get('/api/data/export').json()
    assert exported['profile'] == {'display_name': 'Export Student', 'google_email': 'export@example.com'}

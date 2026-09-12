import json
import logging
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.security import RequestJSONFormatter
from test_checkins import signed_in


def test_body_size_host_and_headers(client):
    assert client.post('/api/check-ins', content='x'*32769).status_code == 413
    def chunks():
        yield b'x'*20000
        yield b'x'*20000
    assert client.post('/api/check-ins', content=chunks()).status_code == 413
    assert client.get('/api/health/live', headers={'Host': 'attacker.invalid'}).status_code == 400
    response = client.get('/api/health/live')
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'


def test_production_csp_static_same_origin_and_docs_disabled(monkeypatch, tmp_path):
    import app.main as main
    (tmp_path/'index.html').write_text('<html><body>Compiled product</body></html>')
    settings = Settings(_env_file=None, app_env='production', cookie_secure=True,
        frontend_origin='https://wellbeing.example', session_secret='a-secure-test-secret-with-at-least-32-characters', frontend_dist=str(tmp_path))
    monkeypatch.setattr(main, 'get_settings', lambda: settings)
    with TestClient(create_app(), base_url='https://wellbeing.example') as client:
        response = client.get('/')
        assert response.status_code == 200 and 'Compiled product' in response.text
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert 'strict-transport-security' in response.headers
        assert client.get('/openapi.json').status_code == 404
        assert client.get('/api/missing').status_code == 404
        assert client.get('/api/health/live').json()['phase'] == 4
        assert client.get('/../.env').status_code == 404


def test_logs_only_allowlisted_metadata():
    record = logging.LogRecord('wellbeing.requests', logging.INFO, '', 0, 'SECRET INPUT', (), None)
    record.request_id = 'request-1'; record.method = 'POST'; record.route = '/api/ai/reflections'
    record.status = 200; record.duration_ms = 5; record.private = 'SECRET TOKEN'
    output = RequestJSONFormatter().format(record)
    assert 'SECRET' not in output
    assert json.loads(output)['route'] == '/api/ai/reflections'


def test_ai_and_ml_are_authenticated_and_csrf_protected(client, signed_in):
    client, _ = signed_in
    client.headers.pop('X-CSRF-Token')
    assert client.patch('/api/ai/consent', json={'enabled': True}).status_code == 403
    assert client.get('/api/ml/status').json()['serving_enabled'] is False
    client.cookies.clear()
    assert client.get('/api/ai/status').status_code == 401
    assert client.get('/api/ml/status').status_code == 401

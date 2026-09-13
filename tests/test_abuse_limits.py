from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.security import AbuseLimitMiddleware
import pytest


@pytest.mark.parametrize('sslmode', ['disable', 'allow', 'prefer'])
def test_production_database_rejects_unencrypted_modes(monkeypatch, sslmode):
    import app.database as database
    from types import SimpleNamespace
    monkeypatch.setattr(database, 'get_settings', lambda: SimpleNamespace(app_env='production', database_url=f'postgresql+psycopg://test:test@localhost/test?sslmode={sslmode}'))
    database.get_engine.cache_clear()
    with pytest.raises(ValueError, match='require TLS'):
        database.get_engine()
    database.get_engine.cache_clear()


def test_settings_errors_do_not_print_supplied_secrets():
    from app.config import Settings
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, app_env='production', session_secret='private-test-sentinel')
    assert 'private-test-sentinel' not in str(error.value)


def test_budget_recovers_and_storage_is_bounded():
    now = [0]
    guard = AbuseLimitMiddleware(None, clock=lambda: now[0])
    assert guard.allow('one', 1)
    assert not guard.allow('one', 1)
    now[0] = 60
    assert guard.allow('one', 1)
    for i in range(5000):
        guard.allow(str(i), 1)
    assert len(guard.buckets) == 4096


def test_export_limited_health_exempt_and_forwarded_headers_do_not_bypass():
    app = FastAPI()
    app.add_middleware(AbuseLimitMiddleware)
    @app.get('/api/data/export')
    def export():
        return {'ok': True}
    @app.get('/api/health/live')
    def health():
        return {'status': 'alive'}
    with TestClient(app) as client:
        for _ in range(6):
            assert client.get('/api/data/export').status_code == 200
        response = client.get('/api/data/export', headers={'X-Forwarded-For': 'different'})
        assert response.status_code == 429
        assert response.headers['Retry-After'] == '60'
        assert client.get('/api/health/live').status_code == 200

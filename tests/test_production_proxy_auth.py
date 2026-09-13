"""Emulate the public HTTPS proxy boundary with real sessions and signed OIDC.

This exercises application behavior, not Netlify's hosted proxy implementation.
"""
from urllib.parse import parse_qs

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app import auth
from app.config import Settings
from app.database import get_db
from app.models import Session as AuthSession
from test_auth import auth_settings, oidc_key, oidc_provider, _begin_oidc

PUBLIC = 'https://stressdetect.netlify.app'
UPSTREAM = 'studentstressdetector-backend.onrender.com'
CALLBACK = PUBLIC + '/api/auth/callback'


class ProxyBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            scope = dict(scope)
            scope['headers'] = [(k, v) for k, v in scope['headers'] if k != b'host'] + [(b'host', UPSTREAM.encode())]
            scope['scheme'] = 'http'  # Upstream TLS termination must not affect callback/cookie policy.
            scope['client'] = ('198.51.100.20', 12345)
        await self.app(scope, receive, send)


def test_google_session_csrf_and_logout_across_proxy(db, auth_settings, oidc_provider, monkeypatch):
    import app.main as main
    settings = Settings(_env_file=None, app_env='production', frontend_origin=PUBLIC,
        backend_host=UPSTREAM, cookie_secure=True, enable_dev_auth=False,
        session_secret='production-test-secret-long-enough-for-validation',
        oidc_client_id=auth_settings.oidc_client_id, oidc_client_secret=auth_settings.oidc_client_secret)
    monkeypatch.setattr(auth, 'get_settings', lambda: settings)
    monkeypatch.setattr(main, 'get_settings', lambda: settings)
    app = main.create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(ProxyBoundary(app), base_url=PUBLIC) as browser:
        config = browser.get('/api/auth/config')
        assert config.json() == {'oidc_enabled': True, 'dev_login_enabled': False}
        assert config.headers['cache-control'] == 'no-store'
        assert browser.post('/api/auth/dev-login', headers={'Origin': PUBLIC}).status_code == 404
        assert browser.get('/api/me').status_code == 401
        assert browser.get('/api/health/ready').status_code == 200
        # Neither the upstream Host nor attacker-supplied forwarding headers determine redirects.
        browser.headers.update({'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Proto': 'http'})
        params = _begin_oidc(browser, oidc_provider)
        assert params['redirect_uri'] == [CALLBACK]
        state_cookie = next(c for c in browser.cookies.jar if c.name == 'oidc_state')
        assert state_cookie.secure and state_cookie.domain == 'stressdetect.netlify.app'
        assert {k.lower(): v for k, v in state_cookie._rest.items()}.get('samesite') == 'lax'
        response = browser.get('/api/auth/callback', params={'state': params['state'][0], 'code': 'test-code'}, follow_redirects=False)
        assert response.status_code == 303, response.text
        assert response.headers['location'] == PUBLIC + '/?signed_in=1'
        assert response.headers['cache-control'] == 'no-store'
        cookie = next(v for v in response.headers.get_list('set-cookie') if v.startswith('wellbeing_session='))
        assert all(v in cookie for v in ('Secure', 'HttpOnly', 'SameSite=lax', 'Path=/'))
        assert 'Domain=' not in cookie
        exchange = next(r for r in oidc_provider['requests'] if r.url.path == '/token')
        assert parse_qs(exchange.content.decode())['redirect_uri'] == [CALLBACK]
        assert 'oidc_state' not in browser.cookies
        me = browser.get('/api/me').json()
        assert browser.get('/api/me').json() == me  # Subsequent navigation keeps the opaque session.
        assert browser.get('/api/check-ins').json()['items'] == []
        for headers in ({}, {'Origin': PUBLIC}, {'Origin': 'https://attacker.invalid', 'X-CSRF-Token': me['csrf_token']}):
            assert browser.patch('/api/settings', json={'timezone': 'UTC'}, headers=headers).status_code == 403
        headers = {'Origin': PUBLIC, 'X-CSRF-Token': me['csrf_token']}
        assert browser.patch('/api/settings', json={'timezone': 'UTC'}, headers=headers).status_code == 200
        assert browser.get('/api/me').json()['timezone'] == 'UTC'
        assert browser.post('/api/auth/logout', headers={'Origin': PUBLIC}).status_code == 403
        logout = browser.post('/api/auth/logout', headers=headers)
        assert logout.status_code == 204
        assert 'Max-Age=0' in logout.headers['set-cookie']
        assert 'wellbeing_session' not in browser.cookies
        assert browser.get('/api/me').status_code == 401
        assert db.scalar(select(AuthSession)) is None
    # Render's own health checks are accepted, arbitrary Host values are not.
    with TestClient(app, base_url='https://' + UPSTREAM) as upstream:
        assert upstream.get('/api/health/live').status_code == 200
        assert upstream.get('/api/health/live', headers={'Host': 'attacker.invalid'}).status_code == 400


@pytest.mark.parametrize('changes', [dict(enable_dev_auth=True), dict(cookie_secure=False), dict(frontend_origin='http://stressdetect.netlify.app')])
def test_unsafe_production_settings_refused(changes):
    values = dict(_env_file=None, app_env='production', frontend_origin=PUBLIC,
        cookie_secure=True, session_secret='production-test-secret-long-enough-for-validation')
    values.update(changes)
    with pytest.raises(ValueError):
        Settings(**values)


@pytest.mark.parametrize('hostname', ['*', '*.onrender.com', 'https://render.example', 'render.example:443', 'render.example/path', 'a..b', '-bad.example'])
def test_backend_host_cannot_broaden_the_allowlist(hostname):
    with pytest.raises(ValueError):
        Settings(_env_file=None, backend_host=hostname)

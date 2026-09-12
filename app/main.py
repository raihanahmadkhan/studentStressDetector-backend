import logging
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from app import auth, checkins, fuzzy, product
from app.security import BodyLimitMiddleware, configure_request_logging
from app.config import get_settings
from app.database import get_db
from app.errors import ApiError, install_error_handlers

logger = logging.getLogger('wellbeing.requests')


def create_app() -> FastAPI:
    settings = get_settings()
    configure_request_logging()
    production = settings.app_env == 'production'
    app = FastAPI(title='Student Workload & Wellbeing API', version='4.0.0',
        docs_url=None if production else '/docs', redoc_url=None if production else '/redoc',
        openapi_url=None if production else '/openapi.json',
        description='Versioned fuzzy stress estimates and personal routine analytics.')
    install_error_handlers(app)
    hosts = [urlsplit(settings.frontend_origin).hostname]
    if settings.backend_host:
        hosts.append(settings.backend_host)
    if not production:
        hosts += ['localhost', '127.0.0.1', 'testserver']
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie='oidc_state',
        max_age=600, same_site='lax', https_only=settings.cookie_secure)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True,
        allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token', 'Idempotency-Key'])

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' https://accounts.google.com"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        route = request.scope.get('route')
        logger.info('request', extra={'request_id': request.state.request_id, 'method': request.method,
            'route': getattr(route, 'path', 'unmatched'), 'status': response.status_code,
            'duration_ms': round((time.perf_counter()-started)*1000, 2)})
        return response

    app.include_router(auth.router)
    app.include_router(checkins.router)
    app.include_router(product.router)


    @app.get('/api/fuzzy-model', tags=['model'])
    def model_specification():
        return {'model_version': fuzzy.MODEL_VERSION, 'spec_hash': fuzzy.SPEC_HASH, 'specification': fuzzy.MODEL_SPEC}

    @app.get('/api/health/live', tags=['health'])
    def live():
        return {'status': 'alive', 'phase': 4}

    @app.get('/api/health/ready', tags=['health'])
    def ready(db: Session = Depends(get_db)):
        revision = db.execute(text('SELECT version_num FROM alembic_version')).scalar_one_or_none()
        if revision != '0004_account_profile':
            raise ApiError(503, 'migration_required', 'Database migrations are not current.')
        return {'status': 'ready', 'database': 'available', 'schema_revision': revision, 'fuzzy_version': fuzzy.MODEL_VERSION,
                'predictions_enabled': False, 'explanations_enabled': False}

    if settings.frontend_dist:
        dist = Path(settings.frontend_dist).resolve()
        if not (dist / 'index.html').is_file():
            raise RuntimeError('FRONTEND_DIST must contain a built frontend index.html')
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    else:
        @app.get('/', include_in_schema=False)
        def root():
            return {'application': 'Student Workload & Wellbeing', 'phase': 4, 'frontend': 'not_bundled'}

    return app


app = create_app()

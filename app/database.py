from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine():
    settings = get_settings()
    options = {'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'}
    if settings.app_env == 'production':
        sslmode = make_url(settings.database_url).query.get('sslmode', 'require')
        if sslmode not in ('require', 'verify-ca', 'verify-full'):
            raise ValueError('Production database connections require TLS')
        options['sslmode'] = sslmode
    return create_engine(
        settings.database_url,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args=options,
    )


def get_db():
    # Each request owns its session. Writes explicitly commit before responding.
    with Session(get_engine(), expire_on_commit=False) as db:
        yield db

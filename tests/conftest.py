"""Real PostgreSQL fixtures; never reset an application database."""
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


@pytest.fixture(scope='session')
def test_engine():
    values = dotenv_values(Path(__file__).resolve().parents[1] / '.env')
    url = os.environ.get('TEST_DATABASE_URL') or values.get('TEST_DATABASE_URL')
    if not url:
        pytest.fail('Set TEST_DATABASE_URL to a dedicated PostgreSQL database whose name ends in _test.')
    parsed = make_url(url)
    if parsed.drivername != 'postgresql+psycopg' or not parsed.database.endswith('_test'):
        pytest.fail('Refusing to modify a database not explicitly named *_test with PostgreSQL.')
    previous = {key: os.environ.get(key) for key in ('DATABASE_URL', 'APP_ENV', 'ENABLE_DEV_AUTH')}
    os.environ.update(DATABASE_URL=url, APP_ENV='test', ENABLE_DEV_AUTH='false')
    from app.config import get_settings
    from app.database import get_engine
    get_settings.cache_clear()
    get_engine.cache_clear()
    command.upgrade(Config('alembic.ini'), 'head')
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def db(test_engine):
    with test_engine.begin() as connection:
        connection.execute(text('TRUNCATE users, engine_versions CASCADE'))
    with Session(test_engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


@pytest.fixture
def client(db, test_engine):
    from app.main import create_app
    from app.database import get_db
    app = create_app()

    def request_db():
        with Session(test_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = request_db
    with TestClient(app, base_url='http://localhost') as client:
        yield client

"""Rehearse the initial migration in a disposable *_test database only."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
url = os.environ.get('TEST_DATABASE_URL') or dotenv_values(root / '.env').get('TEST_DATABASE_URL')
if not url or not make_url(url).database.endswith('_test'):
    raise SystemExit('Refusing migration rehearsal: supply a dedicated *_test PostgreSQL database.')
os.environ['DATABASE_URL'] = url
from app.config import get_settings
get_settings.cache_clear()
config = Config(str(root / 'alembic.ini'))
command.downgrade(config, 'base')
command.upgrade(config, 'head')
command.check(config)
print('Dedicated test database: downgrade, fresh upgrade, and schema comparison passed.')

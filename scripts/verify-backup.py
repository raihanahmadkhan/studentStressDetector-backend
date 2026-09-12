"""pg_dump/pg_restore roundtrip of the dedicated *_test database only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app import fuzzy
from app.models import User, CheckIn, CheckInRevision, EngineVersion, Explanation


def main():
    root = Path(__file__).resolve().parents[1]
    raw = os.environ.get('TEST_DATABASE_URL') or dotenv_values(root/'.env').get('TEST_DATABASE_URL')
    if not raw:
        raise SystemExit('Configure TEST_DATABASE_URL')
    url = make_url(raw)
    if url.drivername != 'postgresql+psycopg' or not url.database.endswith('_test'):
        raise SystemExit('Refusing restore rehearsal outside a dedicated *_test PostgreSQL database')
    binaries = Path(os.environ.get('PG_BIN', r'C:\Program Files\PostgreSQL\18\bin'))
    env = {**os.environ, 'PGHOST': url.host, 'PGPORT': str(url.port or 5432), 'PGUSER': url.username,
           'PGPASSWORD': url.password or '', 'PGDATABASE': url.database, 'PGCONNECT_TIMEOUT': '5'}
    output = root/'.local'/'verification'
    output.mkdir(parents=True, exist_ok=True)
    archive = output/'test-roundtrip.dump'
    engine = create_engine(raw)
    fixture_id = uuid4()
    with Session(engine) as db:
        db.execute(insert(EngineVersion).values(version=fuzzy.MODEL_VERSION, kind='fuzzy', spec_hash=fuzzy.SPEC_HASH, specification=fuzzy.MODEL_SPEC, status='active').on_conflict_do_nothing())
        db.add(User(id=fixture_id, oidc_issuer='backup-test-fixture', oidc_subject=str(fixture_id), history_version=2))
        checkin_id = uuid4()
        db.flush()
        db.add(CheckIn(id=checkin_id, user_id=fixture_id, observation_date=date(2026, 1, 1), timezone='UTC', current_revision=2))
        db.flush()
        for revision in (1, 2):
            inputs = dict(sleep_hours=6+revision, academic_load=5, screen_hours=6, extracurricular_load=4, deadline_pressure=5, recovery=5, reported_strain=6)
            db.add(CheckInRevision(checkin_id=checkin_id, user_id=fixture_id, revision=revision,
                **inputs, questionnaire_version=fuzzy.QUESTIONNAIRE_VERSION, engine_version=fuzzy.MODEL_VERSION, assessment=fuzzy.evaluate(inputs), retrospective=True))
        db.add(Explanation(user_id=fixture_id, history_version=2, source_hash='a'*64,
            source_snapshot={'fixture': 'backup verification only'}, prompt_version='test', model_version='deterministic', status='fallback', output={'fixture': True}))
        db.commit()
    def snapshot():
        with engine.connect() as db:
            tables = db.execute(text("select tablename from pg_tables where schemaname='public' order by tablename")).scalars().all()
            result = {}
            for name in tables:
                quoted = engine.dialect.identifier_preparer.quote(name)
                rows = db.execute(text(f'SELECT row_to_json(t)::text FROM {quoted} t')).scalars().all()
                result[name] = {'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()}
            return result
    before = snapshot()
    if not before:
        raise SystemExit('Run migrations and tests before backup verification')
    # The roundtrip uses meaningful test fixtures left by the suite, including
    # revisions when present. No application database is read or overwritten.
    subprocess.run([str(binaries/'pg_dump'), '--format=custom', '--no-owner', '--no-acl', '--file', str(archive)], env=env, check=True, capture_output=True)
    subprocess.run([str(binaries/'pg_restore'), '--clean', '--if-exists', '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction', '--dbname', url.database, str(archive)], env=env, check=True, capture_output=True)
    after = snapshot()
    if before != after:
        raise SystemExit('Backup verification failed: restored table hashes differ')
    report = {'status': 'passed', 'database_kind': 'dedicated_test', 'tables': before,
              'fixture': 'One synthetic account with two revisions and explanation provenance, in the test database only.',
              'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'limitation': 'Local logical roundtrip, not a managed-host backup or disaster-recovery drill.'}
    (output/'backup-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    with engine.begin() as db:
        db.execute(text('DELETE FROM users WHERE id=:id'), {'id': fixture_id})
    engine.dispose()
    print('Backup/restore roundtrip passed: all table row counts and content hashes match.')


if __name__ == '__main__':
    main()

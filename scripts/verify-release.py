"""Read-only check of an actual staged artifact with production middleware."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from dotenv import dotenv_values
from fastapi.testclient import TestClient

parser = argparse.ArgumentParser()
parser.add_argument('release', type=Path)
args = parser.parse_args()
release = args.release.resolve()
if not (release/'static'/'index.html').is_file() or not (release/'app'/'main.py').is_file():
    raise SystemExit('Not a complete staged release')
for forbidden in ('.env', '.local', 'tests', 'node_modules'):
    if (release/forbidden).exists():
        raise SystemExit('Staged release includes non-release data')
root = Path(__file__).resolve().parents[1]
local = dotenv_values(root/'.env')
if local.get('DATABASE_URL'):
    os.environ['DATABASE_URL'] = local['DATABASE_URL']
os.environ.update(APP_ENV='production', COOKIE_SECURE='true', ENABLE_DEV_AUTH='false',
    FRONTEND_ORIGIN='https://release.example', FRONTEND_DIST=str(release/'static'),
    SESSION_SECRET='artifact-verification-only-not-a-deployment-secret', LLM_ENABLED='false')
sys.path.insert(0, str(release))
from app.main import app
with TestClient(app, base_url='https://release.example') as client:
    page = client.get('/')
    assert page.status_code == 200 and '<div id="root">' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
    assert len(assets) >= 2
    for asset in assets:
        assert client.get(asset).status_code == 200
    assert client.get('/api/health/ready').status_code == 200
    assert client.get('/api/ai/status').status_code == 401
    assert client.get('/openapi.json').status_code == 404
print(json.dumps({'status': 'passed', 'assets_checked': len(assets), 'production_headers': True,
                  'same_origin_api': True, 'deployed': False}))

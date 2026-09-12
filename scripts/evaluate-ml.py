"""Read-only, local, per-user evaluation. Writes aggregate metrics, never trains on fake data."""
import argparse
import json
import sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_engine
from app.ml import dataset, evaluate
from app.ml_data import load_revisions
from app.models import User
from app.checkins import lock_user


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--user-id', type=UUID, help='Evaluate this one local account; never pool users.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with Session(get_engine()) as db:
        if args.user_id:
            user = db.get(User, args.user_id)
            if user is None:
                raise SystemExit('Account not found')
            user = lock_user(db, user, read=True)
            report = evaluate(dataset(load_revisions(db, user.id)))
        else:
            count = len(db.scalars(select(User.id).limit(2)).all())
            if count > 1:
                raise SystemExit('Choose one --user-id; cross-user training is forbidden.')
            user = db.scalar(select(User).limit(1))
            report = evaluate(dataset(load_revisions(db, user.id))) if user else evaluate([])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'eligible_pairs': report['eligible_pairs'], 'serving_enabled': False}))


if __name__ == '__main__':
    main()

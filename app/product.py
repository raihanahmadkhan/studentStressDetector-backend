"""Core product reads and explicit destructive mutations; no background work."""
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
import json

from app import fuzzy
from app.config import get_settings
from app.analytics import summarize
from app.auth import get_current_user, require_csrf, SESSION_COOKIE
from app.checkins import lock_user, find_revision, as_response, payload_hash
from app.database import get_db
from app.errors import ApiError
from app.models import CheckIn, CheckInRevision, EngineVersion, Explanation, MutationReceipt, User, utcnow
from app.schemas import Contract, RoutineInputs, CheckInCreate, Assessment

router = APIRouter(prefix='/api', tags=['core-product'])


class DeleteObservation(Contract):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class DeleteData(Contract):
    expected_history_version: Annotated[int, Field(strict=True, ge=0)]
    confirmation: Literal['DELETE']


class Preferences(Contract):
    timezone: str = Field(min_length=1, max_length=64)
    validate_timezone = field_validator('timezone')(CheckInCreate.iana_timezone.__func__)


class Scenario(Contract):
    inputs: RoutineInputs
    reference_id: UUID | None = None


def checked_receipt(db, user, key, operation, payload):
    digest = payload_hash(operation, payload)
    receipt = db.scalar(select(MutationReceipt).where(MutationReceipt.user_id == user.id, MutationReceipt.mutation_key == key))
    if receipt:
        if receipt.operation != operation or receipt.payload_hash != digest:
            raise ApiError(409, 'idempotency_conflict', 'This request key was already used for different contents.')
        if receipt.expires_at <= utcnow():
            raise ApiError(409, 'retry_window_expired', 'Refresh your history before starting another action.')
    return receipt, digest


def invalidate(db, user):
    user.history_version += 1
    db.execute(delete(Explanation).where(Explanation.user_id == user.id))


@router.delete('/check-ins/{checkin_id}')
def remove_observation(checkin_id: UUID, payload: DeleteObservation, response: Response,
        idempotency_key: UUID = Header(alias='Idempotency-Key'),
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    operation = f'delete:{checkin_id}'
    receipt, digest = checked_receipt(db, user, idempotency_key, operation, payload)
    if receipt:
        response.headers['Idempotency-Replayed'] = 'true'
        return {'deleted': True, 'history_version': receipt.history_version}
    row = find_revision(db, user.id, checkin_id)
    if row is None:
        raise ApiError(404, 'not_found', 'Check-in not found.')
    checkin, revision = row
    if revision.revision != payload.expected_revision:
        raise ApiError(409, 'revision_conflict', 'This observation changed. Reload it before deleting.')
    db.execute(delete(CheckIn).where(CheckIn.id == checkin.id, CheckIn.user_id == user.id))
    invalidate(db, user)
    db.add(MutationReceipt(user_id=user.id, mutation_key=idempotency_key, operation=operation,
        payload_hash=digest, checkin_id=checkin_id, revision=payload.expected_revision,
        history_version=user.history_version, expires_at=utcnow()+timedelta(hours=24)))
    result = {'deleted': True, 'history_version': user.history_version}
    db.commit()
    return result


@router.get('/check-ins/{checkin_id}/revisions')
def revisions(checkin_id: UUID, before: int | None = Query(None, ge=1), limit: int = Query(10, ge=1, le=30),
        expected_history_version: int | None = Query(None, ge=0),
        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    if expected_history_version is not None and expected_history_version != user.history_version:
        raise ApiError(409, 'history_changed', 'History changed. Refresh the revisions.')
    row = find_revision(db, user.id, checkin_id)
    if row is None:
        raise ApiError(404, 'not_found', 'Check-in not found.')
    query = select(CheckInRevision).where(CheckInRevision.checkin_id == checkin_id, CheckInRevision.user_id == user.id)
    if before:
        query = query.where(CheckInRevision.revision < before)
    items = db.scalars(query.order_by(CheckInRevision.revision.desc()).limit(limit+1)).all()
    return {'items': [as_response(row[0], item, user.history_version) for item in items[:limit]],
            'next_cursor': items[limit-1].revision if len(items) > limit else None, 'history_version': user.history_version}


@router.get('/patterns')
def patterns(end_date: date | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    end = end_date or utcnow().astimezone(ZoneInfo(user.timezone)).date()
    if end > utcnow().astimezone(ZoneInfo(user.timezone)).date():
        raise ApiError(422, 'future_date', 'Choose today or an earlier end date.')
    # Six extra days allow complete trailing windows at the beginning of the chart.
    rows = db.execute(select(CheckIn, CheckInRevision).join(CheckInRevision,
        (CheckInRevision.checkin_id == CheckIn.id) & (CheckInRevision.revision == CheckIn.current_revision))
        .where(CheckIn.user_id == user.id, CheckIn.observation_date.between(end-timedelta(days=33), end))).all()
    observations = [{'observation_date': c.observation_date, 'inputs': as_response(c, r, user.history_version).inputs.model_dump()} for c, r in rows]
    return {**summarize(observations, end), 'history_version': user.history_version}


@router.post('/scenarios')
def scenario(payload: Scenario, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    reference = None
    if payload.reference_id:
        row = find_revision(db, user.id, payload.reference_id)
        if row is None:
            raise ApiError(404, 'not_found', 'Reference check-in not found.')
        saved = as_response(*row, user.history_version)
        reference = {'id': saved.id, 'revision': saved.revision, 'inputs': saved.inputs,
                     'assessment': Assessment.model_validate(fuzzy.evaluate(saved.inputs.model_dump()))}
    result = Assessment.model_validate(fuzzy.evaluate(payload.inputs.model_dump()))
    difference = (round(result.score-reference['assessment'].score, 1)
                  if reference and result.status == 'ok' and reference['assessment'].status == 'ok' else None)
    return {'hypothetical': True, 'inputs': payload.inputs, 'assessment': result, 'reference': reference,
            'difference': difference, 'history_version': user.history_version}


@router.patch('/settings')
def preferences(payload: Preferences, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    user.timezone = payload.timezone
    db.commit()
    return {'timezone': user.timezone, 'history_version': user.history_version}


@router.get('/data/export')
def export_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    # Stream a consistent snapshot while holding this user's shared lock. Other
    # users remain independent, and memory does not grow with the export size.
    def chunks():
        yield json.dumps({'format_version': 2, 'profile': {'display_name': user.display_name, 'google_email': user.google_email}, 'timezone': user.timezone, 'history_version': user.history_version, 'llm_consent': user.llm_consent})[:-1] + ',"revisions":['
        query = select(CheckIn, CheckInRevision).join(CheckInRevision, CheckInRevision.checkin_id == CheckIn.id).where(CheckIn.user_id == user.id).order_by(CheckIn.observation_date, CheckInRevision.revision).execution_options(yield_per=20)
        first = True
        versions = set()
        for c, r in db.execute(query):
            yield ('' if first else ',') + as_response(c, r, user.history_version).model_dump_json()
            first = False
            versions.add(r.engine_version)
        yield '],"models":['
        for i, version in enumerate(sorted(versions)):
            model = db.get(EngineVersion, version)
            yield (',' if i else '') + json.dumps({'version': model.version, 'spec_hash': model.spec_hash, 'specification': model.specification})
        yield '],"reflections":['
        first = True
        for reflection in db.scalars(select(Explanation).where(Explanation.user_id == user.id).order_by(Explanation.created_at).execution_options(yield_per=20)):
            record = {'id': str(reflection.id), 'created_at': reflection.created_at.isoformat(),
                'history_version': reflection.history_version, 'source_hash': reflection.source_hash,
                'source_snapshot': reflection.source_snapshot, 'prompt_version': reflection.prompt_version,
                'model_version': reflection.model_version, 'status': reflection.status,
                'output': reflection.output, 'usage': reflection.usage}
            yield ('' if first else ',') + json.dumps(record)
            first = False
        yield ']}'
    return StreamingResponse(chunks(), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="wellbeing-data.json"'})


@router.delete('/data/history')
def clear_history(payload: DeleteData, response: Response,
        idempotency_key: UUID = Header(alias='Idempotency-Key'),
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    receipt, digest = checked_receipt(db, user, idempotency_key, 'delete-history', payload)
    if receipt:
        response.headers['Idempotency-Replayed'] = 'true'
        return {'deleted': True, 'history_version': receipt.history_version}
    if user.history_version != payload.expected_history_version:
        raise ApiError(409, 'history_changed', 'History changed. Refresh before deleting.')
    db.execute(delete(CheckIn).where(CheckIn.user_id == user.id))
    invalidate(db, user)
    db.add(MutationReceipt(user_id=user.id, mutation_key=idempotency_key, operation='delete-history',
        payload_hash=digest, checkin_id=user.id, revision=0, history_version=user.history_version,
        expires_at=utcnow()+timedelta(hours=24)))
    result = {'deleted': True, 'history_version': user.history_version}
    db.commit()
    return result


@router.delete('/data/account', status_code=204)
def clear_account(payload: DeleteData, request: Request, response: Response,
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    if utcnow()-request.state.session.created_at > timedelta(minutes=15):
        raise ApiError(403, 'recent_login_required', 'Sign out and sign in again before deleting your account (within 15 minutes).')
    if user.history_version != payload.expected_history_version:
        raise ApiError(409, 'history_changed', 'History changed. Refresh before deleting your account.')
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path='/', secure=get_settings().cookie_secure, httponly=True, samesite='lax')
    request.session.clear()

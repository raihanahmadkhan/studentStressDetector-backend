"""Transactional observations. Hypothetical inference never calls this module."""
import hashlib
import json
import logging
from datetime import date, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_csrf
from app.database import get_db
from app.errors import ApiError
from app import fuzzy
from app.models import CheckIn, CheckInRevision, EngineVersion, Explanation, MutationReceipt, User, utcnow
from app.schemas import Assessment, CheckInCreate, CheckInEdit, CheckInPage, CheckInResponse, RoutineInputs, HistoricalInputs

router = APIRouter(prefix='/api', tags=['check-ins'])
logger = logging.getLogger(__name__)


def lock_user(db: Session, user: User, *, read=False) -> User:
    # Serialize this user's mutations, not all users. Refresh after waiting for
    # a lock so concurrent requests see the last committed history version.
    locked = db.execute(select(User).where(User.id == user.id).with_for_update(read=read).execution_options(populate_existing=True)).scalar_one_or_none()
    if locked is None:
        raise ApiError(401, 'AUTH_REQUIRED', 'This account is no longer available. Sign in again.')
    return locked


def payload_hash(operation: str, payload) -> str:
    canonical = json.dumps({'operation': operation, 'payload': payload.model_dump(mode='json')}, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def as_response(checkin: CheckIn, revision: CheckInRevision, history_version: int) -> CheckInResponse:
    return CheckInResponse(
        id=checkin.id, observation_date=checkin.observation_date, timezone=checkin.timezone,
        revision=revision.revision, recorded_at=revision.recorded_at.astimezone(timezone.utc), retrospective=revision.retrospective,
        questionnaire_version=revision.questionnaire_version,
        inputs=HistoricalInputs(sleep_hours=float(revision.sleep_hours), academic_load=revision.academic_load,
                             screen_hours=float(revision.screen_hours), extracurricular_load=revision.extracurricular_load,
                             reported_strain=revision.reported_strain, deadline_pressure=revision.deadline_pressure, recovery=revision.recovery),
        assessment=Assessment.model_validate(revision.assessment), history_version=history_version,
    )


def find_revision(db, user_id, checkin_id, revision_number=None):
    stmt = select(CheckIn, CheckInRevision).join(CheckInRevision, (CheckInRevision.checkin_id == CheckIn.id) & (CheckInRevision.revision == (revision_number if revision_number is not None else CheckIn.current_revision))).where(CheckIn.id == checkin_id, CheckIn.user_id == user_id)
    return db.execute(stmt).first()


def replay(db, user, key, operation, digest):
    receipt = db.scalar(select(MutationReceipt).where(MutationReceipt.user_id == user.id, MutationReceipt.mutation_key == key))
    if receipt is None:
        return None
    if receipt.operation != operation or receipt.payload_hash != digest:
        raise ApiError(409, 'idempotency_conflict', 'This request key was already used for different contents.')
    if receipt.expires_at <= utcnow():
        raise ApiError(409, 'retry_window_expired', 'The retry window has expired. Load the saved observation before starting another save.')
    row = find_revision(db, user.id, receipt.checkin_id, receipt.revision)
    if row is None:
        raise ApiError(410, 'observation_deleted', 'The original observation has been deleted; this retry will not recreate it.')
    return as_response(*row, receipt.history_version)


def assess(db, inputs):
    db.execute(insert(EngineVersion).values(version=fuzzy.MODEL_VERSION, kind='fuzzy', spec_hash=fuzzy.SPEC_HASH,
        specification=fuzzy.MODEL_SPEC, status='active', created_at=utcnow()).on_conflict_do_nothing(index_elements=['version']))
    registered = db.get(EngineVersion, fuzzy.MODEL_VERSION)
    if registered.spec_hash != fuzzy.SPEC_HASH or registered.status != 'active':
        raise ApiError(503, 'model_version_conflict', 'The configured model version is unavailable.')
    try:
        return Assessment.model_validate(fuzzy.evaluate(inputs)).model_dump(mode='json')
    except Exception:
        logger.error('fuzzy_assessment_failed', extra={'model_version': fuzzy.MODEL_VERSION})
        return Assessment(status='error', score=None, raw_centroid=None, category=None,
            model_version=fuzzy.MODEL_VERSION, spec_hash=fuzzy.SPEC_HASH, memberships={}, rules=[],
            aggregate={'universe': [], 'membership': []}, reason='inference_unavailable',
            limitations=['The routine-based index is a heuristic, not a medically validated measurement.']).model_dump(mode='json')


def persist_revision(db, user, checkin, inputs, key, operation, digest):
    now = utcnow()
    revision = CheckInRevision(id=uuid4(), checkin_id=checkin.id, user_id=user.id,
        revision=checkin.current_revision, **inputs,
        questionnaire_version=fuzzy.QUESTIONNAIRE_VERSION, engine_version=fuzzy.MODEL_VERSION,
        assessment=assess(db, inputs), recorded_at=now,
        retrospective=checkin.observation_date < now.astimezone(ZoneInfo(checkin.timezone)).date())
    db.add(revision)
    user.history_version += 1
    db.execute(delete(Explanation).where(Explanation.user_id == user.id))
    db.add(MutationReceipt(user_id=user.id, mutation_key=key, operation=operation, payload_hash=digest,
        checkin_id=checkin.id, revision=revision.revision, history_version=user.history_version,
        expires_at=now + timedelta(hours=24)))
    db.flush()
    result = as_response(checkin, revision, user.history_version)
    db.commit()
    return result


@router.post('/check-ins', response_model=CheckInResponse, status_code=201)
def create_checkin(payload: CheckInCreate, response: Response,
        idempotency_key: UUID = Header(alias='Idempotency-Key'),
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    digest = payload_hash('create', payload)
    original = replay(db, user, idempotency_key, 'create', digest)
    if original:
        response.status_code = 200
        response.headers['Idempotency-Replayed'] = 'true'
        return original
    if payload.observation_date > utcnow().astimezone(ZoneInfo(payload.timezone)).date():
        raise ApiError(422, 'future_observation', 'A check-in cannot describe a future calendar day.')
    existing = db.scalar(select(CheckIn.id).where(CheckIn.user_id == user.id, CheckIn.observation_date == payload.observation_date))
    if existing:
        raise ApiError(409, 'daily_checkin_exists', 'A check-in already exists for this date. Open it before editing.', {'checkin_id': str(existing)})
    checkin = CheckIn(id=uuid4(), user_id=user.id, observation_date=payload.observation_date,
        timezone=payload.timezone, current_revision=1)
    db.add(checkin)
    db.flush()
    inputs = payload.model_dump(exclude={'observation_date', 'timezone'})
    return persist_revision(db, user, checkin, inputs, idempotency_key, 'create', digest)


@router.patch('/check-ins/{checkin_id}', response_model=CheckInResponse)
def edit_checkin(checkin_id: UUID, payload: CheckInEdit, response: Response,
        idempotency_key: UUID = Header(alias='Idempotency-Key'),
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    operation = f'edit:{checkin_id}'
    digest = payload_hash(operation, payload)
    original = replay(db, user, idempotency_key, operation, digest)
    if original:
        response.headers['Idempotency-Replayed'] = 'true'
        return original
    checkin = db.scalar(select(CheckIn).where(CheckIn.id == checkin_id, CheckIn.user_id == user.id))
    if checkin is None:
        raise ApiError(404, 'not_found', 'Check-in not found.')
    if checkin.current_revision != payload.expected_revision:
        raise ApiError(409, 'revision_conflict', 'This observation has a newer revision. Load it before saving your changes.')
    checkin.current_revision += 1
    inputs = payload.model_dump(exclude={'expected_revision'})
    return persist_revision(db, user, checkin, inputs, idempotency_key, operation, digest)


@router.get('/check-ins/{checkin_id}', response_model=CheckInResponse)
def get_checkin(checkin_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    row = find_revision(db, user.id, checkin_id)
    if row is None:
        raise ApiError(404, 'not_found', 'Check-in not found.')
    return as_response(*row, user.history_version)


@router.get('/check-ins', response_model=CheckInPage)
def list_checkins(start_date: date | None = None, end_date: date | None = None,
        cursor: date | None = None, limit: int = Query(30, ge=1, le=100),
        expected_history_version: int | None = Query(None, ge=0),
        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = lock_user(db, user, read=True)
    if expected_history_version is not None and expected_history_version != user.history_version:
        raise ApiError(409, 'history_changed', 'History changed. Refresh before loading another page.')
    end = end_date or utcnow().astimezone(ZoneInfo(user.timezone)).date()
    start = start_date or end - timedelta(days=29)
    if end < start or (end - start).days > 365:
        raise ApiError(422, 'invalid_date_range', 'Use an ordered date range of at most 366 days.')
    query = select(CheckIn, CheckInRevision).join(CheckInRevision, (CheckInRevision.checkin_id == CheckIn.id) & (CheckInRevision.revision == CheckIn.current_revision)).where(CheckIn.user_id == user.id, CheckIn.observation_date.between(start, end))
    if cursor:
        query = query.where(CheckIn.observation_date < cursor)
    rows = db.execute(query.order_by(CheckIn.observation_date.desc()).limit(limit + 1)).all()
    return CheckInPage(items=[as_response(*row, user.history_version) for row in rows[:limit]],
        next_cursor=rows[limit-1][0].observation_date.isoformat() if len(rows) > limit else None,
        history_version=user.history_version)

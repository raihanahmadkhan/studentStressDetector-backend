"""Grounded reflections with durable budgets and post-call source revalidation."""
import asyncio
from datetime import date, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_csrf
from app.checkins import lock_user, payload_hash
from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.llm_provider import Selection, select_highlights
from app.models import AIRequest, Explanation, Session as LoginSession, User, utcnow
from app.reflection_facts import PROMPT_VERSION, bundle, digest, render
from app.schemas import Contract

router = APIRouter(prefix='/api/ai', tags=['grounded-reflections'])


class ReflectionRequest(Contract):
    kind: Literal['checkin', 'weekly']
    checkin_id: UUID | None = None
    end_date: date | None = None
    expected_history_version: int = Field(ge=0, strict=True)

    @model_validator(mode='after')
    def exclusive_source(self):
        if self.kind == 'checkin' and (self.checkin_id is None or self.end_date is not None):
            raise ValueError('Check-in reflections need only a checkin_id')
        if self.kind == 'weekly' and (self.end_date is None or self.checkin_id is not None):
            raise ValueError('Weekly reflections need only an end_date')
        return self


class Consent(Contract):
    enabled: bool = Field(strict=True)


def fallback(facts):
    # Include the independent report, routine, and numerical result/availability
    # in a current-day fallback; weekly fallbacks preserve the first measures.
    selected = [facts['facts'][i] for i in (0, 1, 3)] if facts['context']['kind'] == 'checkin' else facts['facts'][:3]
    selection = Selection.model_validate({'highlights': [{'evidence_id': f['id'], 'question_id': f['question_ids'][0]} for f in selected]})
    return render(selection, facts)


def response(output, facts, history_version, source_hash, status, reason=None, explanation_id=None, model='deterministic'):
    return {'id': explanation_id, 'status': status, 'reason': reason, 'output': output,
            'evidence': facts['facts'], 'context': facts['context'], 'history_version': history_version,
            'source_hash': source_hash, 'prompt_version': PROMPT_VERSION, 'model_version': model}


@router.get('/status')
def status(user: User = Depends(get_current_user)):
    settings = get_settings()
    return {'configured': settings.llm_enabled, 'consent': user.llm_consent, 'provider': 'OpenAI',
            'model': settings.llm_model if settings.llm_enabled else None,
            'hourly_limit': settings.llm_hourly_limit, 'daily_limit': settings.llm_daily_limit,
            'predictions_enabled': False}


@router.patch('/consent')
def consent(payload: Consent, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user = lock_user(db, user)
    user.llm_consent = payload.enabled
    if not payload.enabled:
        db.execute(delete(Explanation).where(Explanation.user_id == user.id))
        # Revocation remains observable even if consent is enabled again while
        # an earlier provider request is in flight.
        db.query(AIRequest).filter(AIRequest.user_id == user.id, AIRequest.status == 'pending').update({'status': 'revoked'})
    db.commit()
    return {'consent': user.llm_consent}


@router.post('/reflections')
def reflection(payload: ReflectionRequest, request: Request,
        idempotency_key: UUID = Header(alias='Idempotency-Key'),
        user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    settings = get_settings()
    user = lock_user(db, user)
    if payload.expected_history_version != user.history_version:
        raise ApiError(409, 'history_changed', 'History changed. Reload the source before requesting a reflection.')
    facts = bundle(db, user, payload)
    source_hash = digest({'facts': facts, 'history_version': user.history_version})
    version, user_id, login_id = user.history_version, user.id, request.state.session.id
    if not settings.llm_enabled or not user.llm_consent:
        return response(fallback(facts), facts, version, source_hash, 'fallback',
                        'provider_unconfigured' if not settings.llm_enabled else 'consent_required')
    fingerprint = payload_hash('reflection', payload)
    previous = db.scalar(select(AIRequest).where(AIRequest.user_id == user.id, AIRequest.request_key == idempotency_key))
    if previous:
        if previous.payload_hash != fingerprint or previous.source_hash != source_hash:
            raise ApiError(409, 'idempotency_conflict', 'This reflection request key belongs to different source data.')
        saved = db.get(Explanation, previous.explanation_id) if previous.explanation_id else None
        if saved:
            return response(saved.output, facts, version, source_hash, saved.status,
                            'provider_unavailable_or_invalid' if saved.status == 'fallback' else None,
                            explanation_id=str(saved.id), model=saved.model_version)
        return response(fallback(facts), facts, version, source_hash, 'fallback', 'request_already_reserved')
    cached = db.scalar(select(Explanation).where(Explanation.user_id == user.id,
        Explanation.source_hash == source_hash, Explanation.prompt_version == PROMPT_VERSION,
        Explanation.model_version == settings.llm_model, Explanation.status == 'grounded').limit(1))
    if cached:
        return response(cached.output, facts, version, source_hash, 'grounded', explanation_id=str(cached.id), model=cached.model_version)
    now = utcnow()
    daily, hourly, pending = db.execute(select(
        func.count(AIRequest.id),
        func.count(AIRequest.id).filter(AIRequest.created_at > now-timedelta(hours=1)),
        func.count(AIRequest.id).filter((AIRequest.status == 'pending') & (AIRequest.created_at > now-timedelta(seconds=30)))
    ).where(AIRequest.user_id == user.id, AIRequest.created_at > now-timedelta(days=1))).one()
    if daily >= settings.llm_daily_limit or hourly >= settings.llm_hourly_limit or pending:
        return response(fallback(facts), facts, version, source_hash, 'fallback', 'rate_limited')
    reservation_id = uuid4()
    db.add(AIRequest(id=reservation_id, user_id=user.id, request_key=idempotency_key,
        payload_hash=fingerprint, source_hash=source_hash, status='pending'))
    db.commit()  # Never hold a database lock/connection during the external call.
    outcome, reason, usage = 'grounded', None, {}
    try:
        selected, usage = asyncio.run(select_highlights(facts, settings))
        output = render(selected, facts)
    except Exception:
        # Do not log provider body, exception text, facts, credentials or raw output.
        outcome, reason, output = 'fallback', 'provider_unavailable_or_invalid', fallback(facts)
    user = lock_user(db, user)
    live_login = db.scalar(select(LoginSession.id).where(LoginSession.id == login_id, LoginSession.user_id == user_id, LoginSession.expires_at > utcnow()))
    reservation = db.get(AIRequest, reservation_id, populate_existing=True)
    if not live_login:
        reservation.status = 'discarded'
        db.commit()
        raise ApiError(401, 'AUTH_REQUIRED', 'Sign in again before viewing a reflection.')
    if not user.llm_consent or user.history_version != version or reservation.status != 'pending':
        reservation.status = 'discarded'
        db.commit()
        raise ApiError(409, 'source_changed', 'History or consent changed while the reflection was generated. Reload the source.')
    saved = Explanation(id=uuid4(), user_id=user.id, history_version=version, source_hash=source_hash,
        source_snapshot=facts, prompt_version=PROMPT_VERSION, model_version=settings.llm_model,
        status=outcome, output=output, usage=usage)
    db.add(saved)
    db.flush()
    reservation.status, reservation.explanation_id = outcome, saved.id
    db.commit()
    return response(output, facts, version, source_hash, outcome, reason, str(saved.id), settings.llm_model)

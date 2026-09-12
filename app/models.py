from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (UniqueConstraint('oidc_issuer', 'oidc_subject'), CheckConstraint('history_version >= 0'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    oidc_issuer: Mapped[str] = mapped_column(String(255))
    oidc_subject: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default='Asia/Kolkata')
    history_version: Mapped[int] = mapped_column(default=0, server_default='0')
    llm_consent: Mapped[bool] = mapped_column(default=False, server_default=text('false'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EngineVersion(Base):
    __tablename__ = 'engine_versions'
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    spec_hash: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default='active')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckIn(Base):
    __tablename__ = 'checkins'
    __table_args__ = (
        UniqueConstraint('user_id', 'observation_date', name='uq_checkins_user_date'),
        UniqueConstraint('id', 'user_id', name='uq_checkins_id_user'),
        ForeignKeyConstraint(['id', 'current_revision'], ['checkin_revisions.checkin_id', 'checkin_revisions.revision'], name='fk_checkins_current_revision', deferrable=True, initially='DEFERRED', use_alter=True),
        CheckConstraint('current_revision > 0'),
        Index('ix_checkins_user_date', 'user_id', 'observation_date'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    observation_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckInRevision(Base):
    __tablename__ = 'checkin_revisions'
    __table_args__ = (
        ForeignKeyConstraint(['checkin_id', 'user_id'], ['checkins.id', 'checkins.user_id'], ondelete='CASCADE'),
        UniqueConstraint('checkin_id', 'revision', name='uq_revision_number'),
        CheckConstraint('revision > 0'),
        CheckConstraint('sleep_hours >= 0 AND sleep_hours <= 24 AND mod(sleep_hours, 0.25) = 0'),
        CheckConstraint('screen_hours >= 0 AND screen_hours <= 24 AND mod(screen_hours, 0.25) = 0'),
        CheckConstraint('academic_load BETWEEN 0 AND 10'),
        CheckConstraint('extracurricular_load BETWEEN 0 AND 10'),
        CheckConstraint('reported_strain IS NULL OR reported_strain BETWEEN 0 AND 10'),
        CheckConstraint('deadline_pressure IS NULL OR deadline_pressure BETWEEN 0 AND 10', name='ck_deadline_range'),
        CheckConstraint('recovery IS NULL OR recovery BETWEEN 0 AND 10', name='ck_recovery_range'),
        CheckConstraint("questionnaire_version != 'check-in-2.0.0' OR (deadline_pressure IS NOT NULL AND recovery IS NOT NULL AND sleep_hours <= 12 AND screen_hours <= 16)", name='ck_questionnaire_v2'),
        Index('ix_revisions_user_recorded', 'user_id', 'recorded_at'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    checkin_id: Mapped[UUID]
    user_id: Mapped[UUID]
    revision: Mapped[int]
    sleep_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    academic_load: Mapped[int]
    screen_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    extracurricular_load: Mapped[int]
    deadline_pressure: Mapped[int | None]
    recovery: Mapped[int | None]
    reported_strain: Mapped[int | None]
    questionnaire_version: Mapped[str] = mapped_column(String(50), default='check-in-1.0.0')
    engine_version: Mapped[str] = mapped_column(ForeignKey('engine_versions.version'))
    assessment: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retrospective: Mapped[bool]


class Explanation(Base):
    """Validated reflections, invalidated when their source history changes."""
    __tablename__ = 'explanations'
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    history_version: Mapped[int]
    source_hash: Mapped[str] = mapped_column(String(64))
    source_snapshot: Mapped[dict] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    output: Mapped[dict | None] = mapped_column(JSONB)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    feedback: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MutationReceipt(Base):
    __tablename__ = 'mutation_receipts'
    __table_args__ = (UniqueConstraint('user_id', 'mutation_key', name='uq_receipt_user_key'),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    mutation_key: Mapped[UUID]
    operation: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    # No foreign key: minimal receipt survives observation deletion for retry safety.
    checkin_id: Mapped[UUID]
    revision: Mapped[int]
    history_version: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AIRequest(Base):
    """Minimal durable quota reservations survive edits and history deletion."""
    __tablename__ = 'ai_requests'
    __table_args__ = (UniqueConstraint('user_id', 'request_key', name='uq_ai_user_key'),
                     Index('ix_ai_user_created', 'user_id', 'created_at'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    request_key: Mapped[UUID]
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    explanation_id: Mapped[UUID | None] = mapped_column(ForeignKey('explanations.id', ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

"""Foundation schema

Revision ID: 0001_foundation
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_foundation'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('engine_versions',
    sa.Column('version', sa.String(length=80), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('spec_hash', sa.String(length=64), nullable=False),
    sa.Column('specification', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('version')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('oidc_issuer', sa.String(length=255), nullable=False),
    sa.Column('oidc_subject', sa.String(length=255), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('history_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('llm_consent', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('history_version >= 0'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('oidc_issuer', 'oidc_subject')
    )
    op.create_table('checkins',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('observation_date', sa.Date(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('current_revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('current_revision > 0'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_checkins_id_user'),
    sa.UniqueConstraint('user_id', 'observation_date', name='uq_checkins_user_date')
    )
    op.create_index('ix_checkins_user_date', 'checkins', ['user_id', 'observation_date'], unique=False)
    op.create_table('explanations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('history_version', sa.Integer(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('source_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=False),
    sa.Column('model_version', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('feedback', sa.String(length=30), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_explanations_user_id'), 'explanations', ['user_id'], unique=False)
    op.create_table('mutation_receipts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('mutation_key', sa.Uuid(), nullable=False),
    sa.Column('operation', sa.String(length=100), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('checkin_id', sa.Uuid(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('history_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'mutation_key', name='uq_receipt_user_key')
    )
    op.create_index(op.f('ix_mutation_receipts_expires_at'), 'mutation_receipts', ['expires_at'], unique=False)
    op.create_index(op.f('ix_mutation_receipts_user_id'), 'mutation_receipts', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('checkin_revisions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('checkin_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('sleep_hours', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('academic_load', sa.Integer(), nullable=False),
    sa.Column('screen_hours', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('extracurricular_load', sa.Integer(), nullable=False),
    sa.Column('reported_strain', sa.Integer(), nullable=True),
    sa.Column('questionnaire_version', sa.String(length=50), nullable=False),
    sa.Column('engine_version', sa.String(length=80), nullable=False),
    sa.Column('assessment', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retrospective', sa.Boolean(), nullable=False),
    sa.CheckConstraint('academic_load BETWEEN 0 AND 10'),
    sa.CheckConstraint('extracurricular_load BETWEEN 0 AND 10'),
    sa.CheckConstraint('reported_strain IS NULL OR reported_strain BETWEEN 0 AND 10'),
    sa.CheckConstraint('revision > 0'),
    sa.CheckConstraint('screen_hours >= 0 AND screen_hours <= 24 AND mod(screen_hours, 0.25) = 0'),
    sa.CheckConstraint('sleep_hours >= 0 AND sleep_hours <= 24 AND mod(sleep_hours, 0.25) = 0'),
    sa.ForeignKeyConstraint(['checkin_id', 'user_id'], ['checkins.id', 'checkins.user_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['engine_version'], ['engine_versions.version'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('checkin_id', 'revision', name='uq_revision_number')
    )
    op.create_index('ix_revisions_user_recorded', 'checkin_revisions', ['user_id', 'recorded_at'], unique=False)
    # Install after both tables exist. Deferred checking permits atomic revision creation.
    op.create_foreign_key('fk_checkins_current_revision', 'checkins', 'checkin_revisions',
        ['id', 'current_revision'], ['checkin_id', 'revision'], deferrable=True, initially='DEFERRED')

def downgrade():
    op.drop_constraint('fk_checkins_current_revision', 'checkins', type_='foreignkey')
    op.drop_index('ix_revisions_user_recorded', table_name='checkin_revisions')
    op.drop_table('checkin_revisions')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_mutation_receipts_user_id'), table_name='mutation_receipts')
    op.drop_index(op.f('ix_mutation_receipts_expires_at'), table_name='mutation_receipts')
    op.drop_table('mutation_receipts')
    op.drop_index(op.f('ix_explanations_user_id'), table_name='explanations')
    op.drop_table('explanations')
    op.drop_index('ix_checkins_user_date', table_name='checkins')
    op.drop_table('checkins')
    op.drop_table('users')
    op.drop_table('engine_versions')

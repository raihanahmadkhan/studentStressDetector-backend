"""Durable per-user LLM quota reservations; no raw prompt in the ledger."""
from alembic import op
import sqlalchemy as sa

revision = '0002_ai_requests'
down_revision = '0001_foundation'
branch_labels = depends_on = None


def upgrade():
    op.create_table('ai_requests',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('explanation_id', sa.Uuid(), sa.ForeignKey('explanations.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'request_key', name='uq_ai_user_key'))
    op.create_index('ix_ai_user_created', 'ai_requests', ['user_id', 'created_at'])


def downgrade():
    op.drop_index('ix_ai_user_created', table_name='ai_requests')
    op.drop_table('ai_requests')

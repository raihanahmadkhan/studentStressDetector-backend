"""Nullable legacy observations; strict new questionnaire domains."""
from alembic import op
import sqlalchemy as sa
revision = '0003_checkin_inputs'
down_revision = '0002_ai_requests'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('checkin_revisions', sa.Column('deadline_pressure', sa.Integer(), nullable=True))
    op.add_column('checkin_revisions', sa.Column('recovery', sa.Integer(), nullable=True))
    op.create_check_constraint('ck_deadline_range', 'checkin_revisions', 'deadline_pressure IS NULL OR deadline_pressure BETWEEN 0 AND 10')
    op.create_check_constraint('ck_recovery_range', 'checkin_revisions', 'recovery IS NULL OR recovery BETWEEN 0 AND 10')
    op.create_check_constraint('ck_questionnaire_v2', 'checkin_revisions', "questionnaire_version != 'check-in-2.0.0' OR (deadline_pressure IS NOT NULL AND recovery IS NOT NULL AND sleep_hours <= 12 AND screen_hours <= 16)")

def downgrade():
    for name in ('ck_questionnaire_v2', 'ck_recovery_range', 'ck_deadline_range'):
        op.drop_constraint(name, 'checkin_revisions', type_='check')
    op.drop_column('checkin_revisions', 'recovery')
    op.drop_column('checkin_revisions', 'deadline_pressure')

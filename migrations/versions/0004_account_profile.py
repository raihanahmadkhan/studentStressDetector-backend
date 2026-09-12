"""Optional account details for existing and new Google identities."""
from alembic import op
import sqlalchemy as sa

revision = '0004_account_profile'
down_revision = '0003_checkin_inputs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('display_name', sa.String(80), nullable=True))
    op.add_column('users', sa.Column('google_email', sa.String(320), nullable=True))


def downgrade():
    op.drop_column('users', 'google_email')
    op.drop_column('users', 'display_name')

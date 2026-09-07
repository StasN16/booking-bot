"""Split reminder flag and store customer language

Appointments need two reminders, 24 hours and 1 hour ahead, and a single
reminder_sent boolean cannot record which of them went out. The old flag is
carried into reminder_24h_sent so appointments already reminded are not
messaged a second time.

Revision ID: 1f97a0651b28
Revises: 017dd418cff6
Create Date: 2026-09-07 08:26:47.059775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f97a0651b28'
down_revision: Union[str, Sequence[str], None] = '017dd418cff6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required: PostgreSQL rejects a NOT NULL column added
    # to a table that already holds rows unless existing rows get a value.
    op.add_column(
        'appointments',
        sa.Column('reminder_24h_sent', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        'appointments',
        sa.Column('reminder_1h_sent', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    # Preserve history: anything already reminded counts as its 24h reminder.
    op.execute("UPDATE appointments SET reminder_24h_sent = reminder_sent")

    op.drop_column('appointments', 'reminder_sent')

    op.add_column('customers', sa.Column('language', sa.String(length=5), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'appointments',
        sa.Column('reminder_sent', sa.BOOLEAN(), nullable=False,
                  server_default=sa.false()),
    )
    op.execute("UPDATE appointments SET reminder_sent = reminder_24h_sent")

    op.drop_column('appointments', 'reminder_1h_sent')
    op.drop_column('appointments', 'reminder_24h_sent')
    op.drop_column('customers', 'language')

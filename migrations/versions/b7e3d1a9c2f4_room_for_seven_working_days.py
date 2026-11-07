"""Room for a therapist who works all seven days

Working days are stored as English day names joined by commas, and all
seven come to 56 characters. The column held 50, so saving someone who
works every day failed with a database error.

Revision ID: b7e3d1a9c2f4
Revises: 9c4f2a7d1b3e
Create Date: 2026-10-08 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3d1a9c2f4'
down_revision: Union[str, Sequence[str], None] = '9c4f2a7d1b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('therapists', 'working_days',
                    existing_type=sa.String(length=50), type_=sa.String(length=100),
                    existing_nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Someone working all seven days would not fit back: drop their last
    # day, whole, rather than cut a day name in half.
    op.execute("UPDATE therapists SET working_days = regexp_replace(working_days, ',[^,]*$', '') "
               "WHERE length(working_days) > 50")
    op.alter_column('therapists', 'working_days',
                    existing_type=sa.String(length=100), type_=sa.String(length=50),
                    existing_nullable=True)

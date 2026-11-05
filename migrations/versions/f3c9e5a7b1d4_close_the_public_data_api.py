"""Close every table to Supabase's public data API

Supabase answers its REST API for any table in the public schema, as the
anon and authenticated roles, using a key it considers safe to publish. The
bot never uses that API, so nothing should be reachable through it. Row
level security with no policies refuses those roles every row.

The app itself is unaffected: it connects as the tables' owner, and an
owner is not subject to row level security unless it is forced, which it
is not. On a database outside Supabase this changes nothing at all.

A table added later must do the same in its own migration; the integration
test checks every table.

Revision ID: f3c9e5a7b1d4
Revises: e8b2d4f6a1c3
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3c9e5a7b1d4'
down_revision: Union[str, Sequence[str], None] = 'e8b2d4f6a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    'alembic_version', 'appointments', 'businesses', 'customers', 'google_tokens',
    'therapist_treatments', 'therapists', 'treatments', 'users',
)


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

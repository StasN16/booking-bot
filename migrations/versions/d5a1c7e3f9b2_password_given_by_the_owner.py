"""A clinic user given a password by the owner must choose their own

The owner hands every new clinic user the same start password. Until the
user replaces it, the user may sign in and change it, and nothing else.
Users who exist already chose nothing yet either, but they were given
random passwords, so they are left as they are.

Revision ID: d5a1c7e3f9b2
Revises: b7e3d1a9c2f4
Create Date: 2026-10-08 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5a1c7e3f9b2'
down_revision: Union[str, Sequence[str], None] = 'b7e3d1a9c2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(),
                                     server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'must_change_password')

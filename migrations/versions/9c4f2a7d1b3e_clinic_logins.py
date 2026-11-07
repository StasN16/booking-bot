"""Clinic logins, and one WhatsApp number per clinic

One server now runs many clinics. Each clinic signs in to the dashboard
with its own logins, kept in a new users table, and receives its WhatsApp
messages by the number they are sent to, so no two clinics may share one.

Revision ID: 9c4f2a7d1b3e
Revises: 1f97a0651b28
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4f2a7d1b3e'
down_revision: Union[str, Sequence[str], None] = '1f97a0651b28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_users_business_id', 'users', ['business_id'])

    # Clinics without a number yet are allowed, and any number of them.
    op.create_index(
        'ix_businesses_whatsapp_phone_id', 'businesses', ['whatsapp_phone_id'],
        unique=True, postgresql_where=sa.text('whatsapp_phone_id IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_businesses_whatsapp_phone_id', table_name='businesses')
    op.drop_index('ix_users_business_id', table_name='users')
    op.drop_table('users')

"""Who does which treatment

therapist_treatments existed from the first migration but nothing used it,
so it allowed empty and repeated rows, and it blocked deleting a therapist
or a treatment that had any. Now each pair appears once, and goes when
either side is deleted.

Revision ID: e8b2d4f6a1c3
Revises: d5a1c7e3f9b2
Create Date: 2026-10-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e8b2d4f6a1c3'
down_revision: Union[str, Sequence[str], None] = 'd5a1c7e3f9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'therapist_treatments'


def upgrade() -> None:
    """Upgrade schema."""
    # Nothing wrote here, but clear anything a primary key would refuse.
    op.execute(f"DELETE FROM {TABLE} WHERE therapist_id IS NULL OR treatment_id IS NULL")
    op.execute(f"""
        DELETE FROM {TABLE} a USING {TABLE} b
        WHERE a.ctid < b.ctid AND a.therapist_id = b.therapist_id AND a.treatment_id = b.treatment_id
    """)
    op.alter_column(TABLE, 'therapist_id', nullable=False)
    op.alter_column(TABLE, 'treatment_id', nullable=False)
    op.create_primary_key('therapist_treatments_pkey', TABLE, ['therapist_id', 'treatment_id'])

    # PostgreSQL's own names for the keys the first migration made.
    op.drop_constraint('therapist_treatments_therapist_id_fkey', TABLE, type_='foreignkey')
    op.drop_constraint('therapist_treatments_treatment_id_fkey', TABLE, type_='foreignkey')
    op.create_foreign_key('therapist_treatments_therapist_id_fkey', TABLE, 'therapists',
                          ['therapist_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('therapist_treatments_treatment_id_fkey', TABLE, 'treatments',
                          ['treatment_id'], ['id'], ondelete='CASCADE')
    # The primary key starts with therapist_id; this serves "who does X".
    op.create_index('ix_therapist_treatments_treatment_id', TABLE, ['treatment_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_therapist_treatments_treatment_id', table_name=TABLE)
    op.drop_constraint('therapist_treatments_therapist_id_fkey', TABLE, type_='foreignkey')
    op.drop_constraint('therapist_treatments_treatment_id_fkey', TABLE, type_='foreignkey')
    op.create_foreign_key('therapist_treatments_therapist_id_fkey', TABLE, 'therapists',
                          ['therapist_id'], ['id'])
    op.create_foreign_key('therapist_treatments_treatment_id_fkey', TABLE, 'treatments',
                          ['treatment_id'], ['id'])
    op.drop_constraint('therapist_treatments_pkey', TABLE, type_='primary')
    op.alter_column(TABLE, 'therapist_id', nullable=True)
    op.alter_column(TABLE, 'treatment_id', nullable=True)

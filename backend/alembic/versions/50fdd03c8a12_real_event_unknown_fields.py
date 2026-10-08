"""Retain real events with unknown readers/doors or invalid device time.

Revision ID: 50fdd03c8a12
Revises: 31853c250e8f
"""

from alembic import op

revision = "50fdd03c8a12"
down_revision = "31853c250e8f"
branch_labels = None
depends_on = None


def upgrade():
    for column in ("at", "door", "reader_id"):
        op.alter_column("access_events", column, nullable=True)


def downgrade():
    # Refuse destructive downgrade if real events contain unknown fields.
    for column in ("at", "door", "reader_id"):
        op.alter_column("access_events", column, nullable=False)

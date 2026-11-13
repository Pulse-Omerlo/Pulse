# AI contribution: 50% or more AI-generated
"""Record who decided a match candidate, when, and what accepting it changed.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-12 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("match_candidate", sa.Column("decided_by", sa.Text(), nullable=True))
    op.add_column(
        "match_candidate", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "match_candidate",
        sa.Column("decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_candidate", "decision")
    op.drop_column("match_candidate", "decided_at")
    op.drop_column("match_candidate", "decided_by")

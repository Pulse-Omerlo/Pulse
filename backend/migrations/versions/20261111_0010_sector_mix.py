# AI contribution: 50% or more AI-generated
"""Store a network's sector mix and gaps per sync run.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def jsonb(name: str, empty: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(f"'{empty}'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "sector_mix",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("sync_run_id", sa.UUID(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        jsonb("settings", "{}"),
        sa.Column("organizations", sa.Integer(), nullable=False),
        sa.Column("classified", sa.Integer(), nullable=False),
        sa.Column("people_status", sa.Text(), nullable=False),
        sa.Column("people", sa.Integer(), nullable=False),
        jsonb("sectors", "[]"),
        jsonb("gaps", "[]"),
        jsonb("unclassified", "[]"),
        jsonb("people_unplaced", "[]"),
        sa.CheckConstraint(
            "people_status IN ('linked', 'not_linked')",
            name=op.f("ck_sector_mix_people_status"),
        ),
        sa.CheckConstraint(
            "classified <= organizations", name=op.f("ck_sector_mix_classified_within_total")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sectors) = 'array'", name=op.f("ck_sector_mix_sectors_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(gaps) = 'array'", name=op.f("ck_sector_mix_gaps_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sync_run_id"],
            ["sync_run.tenant_id", "sync_run.id"],
            name=op.f("fk_sector_mix_tenant_id_sync_run_id_sync_run"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_sector_mix_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sector_mix")),
        sa.UniqueConstraint(
            "tenant_id", "sync_run_id", name=op.f("uq_sector_mix_tenant_id_sync_run_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("sector_mix")

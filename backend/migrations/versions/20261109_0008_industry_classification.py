# AI contribution: 50% or more AI-generated
"""Store the NAICS sectors of organization source records.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industry_classification",
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("primary_sector", sa.Text(), nullable=True),
        sa.Column(
            "sectors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('classified', 'unclassified')",
            name=op.f("ck_industry_classification_status"),
        ),
        sa.CheckConstraint(
            "(status = 'classified') = (primary_sector IS NOT NULL)",
            name=op.f("ck_industry_classification_primary_when_classified"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sectors) = 'array'",
            name=op.f("ck_industry_classification_sectors_is_array"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_record_id"],
            ["source_record.tenant_id", "source_record.id"],
            name=op.f("fk_industry_classification_tenant_id_source_record_id_source_record"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_industry_classification_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("source_record_id", name=op.f("pk_industry_classification")),
    )
    op.create_index(
        "ix_industry_classification_primary_sector",
        "industry_classification",
        ["tenant_id", "primary_sector"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_industry_classification_primary_sector", table_name="industry_classification")
    op.drop_table("industry_classification")

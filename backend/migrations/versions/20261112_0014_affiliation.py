# AI contribution: 50% or more AI-generated
"""Link people to the organizations they work for.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliation",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("person_record_id", sa.UUID(), nullable=False),
        sa.Column("organization_record_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IN ('relation', 'email_domain', 'job_title')",
            name=op.f("ck_affiliation_method"),
        ),
        sa.CheckConstraint("status IN ('linked', 'conflict')", name=op.f("ck_affiliation_status")),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_affiliation_confidence_range")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name=op.f("ck_affiliation_evidence_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_record_id"],
            ["source_record.tenant_id", "source_record.id"],
            name=op.f("fk_affiliation_tenant_id_person_record_id_source_record"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organization_record_id"],
            ["source_record.tenant_id", "source_record.id"],
            name=op.f("fk_affiliation_tenant_id_organization_record_id_source_record"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_affiliation_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliation")),
        sa.UniqueConstraint(
            "tenant_id",
            "person_record_id",
            "organization_record_id",
            name=op.f("uq_affiliation_tenant_id_person_record_id_organization_record_id"),
        ),
    )
    op.create_index(
        "ix_affiliation_organization_record",
        "affiliation",
        ["tenant_id", "organization_record_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_affiliation_organization_record", table_name="affiliation")
    op.drop_table("affiliation")

# AI contribution: 50% or more AI-generated
"""Add person and person_link for Tier 1 people matching.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_person_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_person_tenant_id_id")),
    )
    op.create_table(
        "person_link",
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IN ('email', 'phone', 'linkedin', 'organization_id', 'manual')",
            name=op.f("ck_person_link_method"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_person_link_confidence_range")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name=op.f("ck_person_link_evidence_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["person.tenant_id", "person.id"],
            name=op.f("fk_person_link_tenant_id_person_id_person"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_record_id"],
            ["source_record.tenant_id", "source_record.id"],
            name=op.f("fk_person_link_tenant_id_source_record_id_source_record"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_person_link_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("source_record_id", name=op.f("pk_person_link")),
    )
    op.create_index(
        "ix_person_link_person", "person_link", ["tenant_id", "person_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_person_link_person", table_name="person_link")
    op.drop_table("person_link")
    op.drop_table("person")

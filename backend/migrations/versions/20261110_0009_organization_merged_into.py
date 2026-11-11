# AI contribution: 50% or more AI-generated
"""Point a merged organization at the organization it was merged into.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organization", sa.Column("merged_into", sa.UUID(), nullable=True))
    op.create_index(
        "ix_organization_merged_into", "organization", ["tenant_id", "merged_into"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_organization_tenant_id_merged_into_organization"),
        "organization",
        "organization",
        ["tenant_id", "merged_into"],
        ["tenant_id", "id"],
    )
    op.create_check_constraint(
        op.f("ck_organization_not_merged_into_itself"),
        "organization",
        "merged_into IS NULL OR merged_into <> id",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_organization_not_merged_into_itself"), "organization", type_="check"
    )
    op.drop_constraint(
        op.f("fk_organization_tenant_id_merged_into_organization"),
        "organization",
        type_="foreignkey",
    )
    op.drop_index("ix_organization_merged_into", table_name="organization")
    op.drop_column("organization", "merged_into")

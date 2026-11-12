# AI contribution: 50% or more AI-generated
"""Allow fuzzy as an organization link method.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-10 20:56:00.785407
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIER1_METHODS = "'domain', 'linkedin', 'external_id', 'name_city'"
REMOVE_FUZZY_LINKS = sa.text(
    "DELETE FROM org_link WHERE method = 'fuzzy' RETURNING organization_id"
)
# Organizations that only had fuzzy links and that nothing was merged into.
REMOVE_EMPTY_ORGANIZATIONS = sa.text(
    """
    DELETE FROM organization o
    WHERE o.id = ANY(:ids)
      AND NOT EXISTS (
        SELECT 1 FROM org_link l WHERE l.tenant_id = o.tenant_id AND l.organization_id = o.id
      )
      AND NOT EXISTS (
        SELECT 1 FROM organization m WHERE m.tenant_id = o.tenant_id AND m.merged_into = o.id
      )
    """
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_org_link_method"), "org_link", type_="check")
    op.create_check_constraint(
        op.f("ck_org_link_method"),
        "org_link",
        f"method IN ({TIER1_METHODS}, 'fuzzy', 'manual')",
    )


def downgrade() -> None:
    # Fuzzy links cannot satisfy the narrower constraint; matching can find them again.
    bind = op.get_bind()
    organization_ids = sorted({row[0] for row in bind.execute(REMOVE_FUZZY_LINKS)})
    if organization_ids:
        bind.execute(REMOVE_EMPTY_ORGANIZATIONS, {"ids": organization_ids})
    op.drop_constraint(op.f("ck_org_link_method"), "org_link", type_="check")
    op.create_check_constraint(
        op.f("ck_org_link_method"), "org_link", f"method IN ({TIER1_METHODS}, 'manual')"
    )

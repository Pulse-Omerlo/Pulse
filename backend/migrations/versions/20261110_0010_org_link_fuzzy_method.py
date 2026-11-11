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
FUZZY_LINKS = sa.text("SELECT count(*) FROM org_link WHERE method = 'fuzzy'")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_org_link_method"), "org_link", type_="check")
    op.create_check_constraint(
        op.f("ck_org_link_method"),
        "org_link",
        f"method IN ({TIER1_METHODS}, 'fuzzy', 'manual')",
    )


def downgrade() -> None:
    # Deleting the links would leave their organizations empty; ask for a decision instead.
    fuzzy = op.get_bind().scalar(FUZZY_LINKS)
    if fuzzy:
        raise RuntimeError(
            f"{fuzzy} organization links use the fuzzy method; "
            "delete or relink them before downgrading below revision 0010"
        )
    op.drop_constraint(op.f("ck_org_link_method"), "org_link", type_="check")
    op.create_check_constraint(
        op.f("ck_org_link_method"), "org_link", f"method IN ({TIER1_METHODS}, 'manual')"
    )

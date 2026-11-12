# AI contribution: 50% or more AI-generated
"""Store event sessions as source records.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-10 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITY_KINDS = "'organization', 'person', 'project', 'event', 'content'"
REFERENCE_KINDS = (
    "'workspace', 'category', 'organization_metadata', 'person_metadata', "
    "'project_metadata', 'event_metadata'"
)


def replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def upgrade() -> None:
    replace_check("source_record", "kind", f"kind IN ({ENTITY_KINDS}, 'event_session')")
    # Same kinds as before, in the order the model lists them.
    replace_check(
        "raw_record",
        "entity_kind",
        f"entity_kind IN ({ENTITY_KINDS}, 'event_session', {REFERENCE_KINDS}, "
        "'content_blocks', 'import_file')",
    )


def downgrade() -> None:
    # Session versions cannot satisfy the narrower constraint; only changes point at them.
    op.execute(
        "DELETE FROM record_change WHERE source_record_id IN "
        "(SELECT id FROM source_record WHERE kind = 'event_session')"
    )
    op.execute("DELETE FROM source_record WHERE kind = 'event_session'")
    replace_check("source_record", "kind", f"kind IN ({ENTITY_KINDS})")
    replace_check(
        "raw_record",
        "entity_kind",
        f"entity_kind IN ({ENTITY_KINDS}, {REFERENCE_KINDS}, 'event_session', "
        "'content_blocks', 'import_file')",
    )

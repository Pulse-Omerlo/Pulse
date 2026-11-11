# AI contribution: 50% or more AI-generated
"""Choose an organization's display name and primary domain from its records."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

OLDEST = datetime.min.replace(tzinfo=UTC)


class ValueSource(Protocol):
    id: UUID
    name: str | None
    domain: str | None
    updated: datetime | None


def most_common(values: Iterable[tuple[str | None, datetime | None, UUID]]) -> str | None:
    """The most frequent value; a tie goes to the value of the most recently updated record.

    Records are compared by update time, then by id, so the choice never depends on order.
    """
    counts = defaultdict(int)
    newest = {}
    for value, updated, record_id in values:
        if not value:
            continue
        counts[value] += 1
        key = (updated or OLDEST, record_id)
        newest[value] = max(newest.get(value, key), key)
    if not counts:
        return None
    return max(counts, key=lambda value: (counts[value], newest[value]))


def choose_display_values(members: Iterable[ValueSource]) -> tuple[str | None, str | None]:
    """Display name and primary domain; members carry generic domains as None."""
    members = list(members)
    name = most_common((m.name, m.updated, m.id) for m in members)
    domain = most_common((m.domain, m.updated, m.id) for m in members)
    return name, domain

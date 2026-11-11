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
    # The record's own name, without fallbacks.
    name: str | None
    name_normalized: str | None
    external_id: str | None
    # Generic domains arrive as None.
    domain: str | None
    updated: datetime | None


def _recency(source: ValueSource) -> tuple[datetime, UUID]:
    return (source.updated or OLDEST, source.id)


def most_common(values: Iterable[tuple[str | None, datetime | None, UUID]]) -> str | None:
    """The most frequent value; a tie goes to the value of the most recently updated record.

    Records are compared by update time, then by id, so the choice never depends on order.
    Missing values do not vote.
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


def choose_display_name(members: Iterable[ValueSource]) -> str | None:
    """The name most members give, counting spellings of one normalized name together.

    The winning name shows its most common spelling. Without any name, the most common
    normalized name, then external id, is used.
    """
    members = list(members)
    spellings = defaultdict(list)
    for member in members:
        if member.name:
            spellings[member.name_normalized or member.name.casefold()].append(member)
    if spellings:
        winner = max(
            spellings.values(),
            key=lambda group: (len(group), max(_recency(member) for member in group)),
        )
        return most_common((m.name, m.updated, m.id) for m in winner)
    return most_common((m.name_normalized, m.updated, m.id) for m in members) or most_common(
        (m.external_id, m.updated, m.id) for m in members
    )


def choose_primary_domain(members: Iterable[ValueSource]) -> str | None:
    return most_common((m.domain, m.updated, m.id) for m in members)


def choose_display_values(members: Iterable[ValueSource]) -> tuple[str | None, str | None]:
    members = list(members)
    return choose_display_name(members), choose_primary_domain(members)

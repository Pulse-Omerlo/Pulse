# AI contribution: 50% or more AI-generated
"""What Tier 2 needs from one record, read once instead of once per compared pair."""

from dataclasses import dataclass
from uuid import UUID

from app.models import SourceRecord
from app.processing.fuzzy_config import FuzzyConfig
from app.processing.match_rules import Fields, Sides
from app.processing.normalization import email_domain


@dataclass(frozen=True)
class Signals:
    """What else, besides the name, can confirm or contradict a fuzzy match."""

    cities: frozenset[str] = frozenset()
    phones: frozenset[str] = frozenset()
    email_domains: frozenset[str] = frozenset()
    domain: str | None = None
    linkedin: str | None = None


@dataclass(frozen=True)
class Profile:
    id: UUID
    name: str
    numbers: frozenset[str]
    # Words that are not generic, in name order, at most max_name_words of them.
    distinctive: tuple[str, ...]
    # Every word of the name, at most max_name_words, for the word-by-word comparison.
    words: tuple[str, ...]
    # Words long enough, distinctive and not numbers: what the name blocks on.
    block_words: frozenset[str]
    signals: Signals


# For a name compared on its own, without a record.
NO_ID = UUID(int=0)
NO_SIGNALS = Signals()


def _strings(values: object) -> frozenset[str]:
    if not isinstance(values, list):
        return frozenset()
    return frozenset(value for value in values if isinstance(value, str) and value)


def signals(record: SourceRecord, excluded_domains: frozenset[str]) -> Signals:
    attributes = record.attributes or {}
    identifiers = attributes.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, dict) else {}
    cities = _strings(attributes.get("cities")) | ({record.city} if record.city else set())
    emails = (email_domain(value) for value in _strings(identifiers.get("emails")))
    linkedin = record.linkedin if (record.linkedin or "").startswith("company/") else None
    return Signals(
        cities=frozenset(cities),
        phones=_strings(identifiers.get("phones")),
        email_domains=frozenset(d for d in emails if d and d not in excluded_domains),
        domain=record.domain if record.domain not in excluded_domains else None,
        linkedin=linkedin,
    )


def profile(record: SourceRecord, config: FuzzyConfig, excluded_domains: frozenset[str]) -> Profile:
    return name_profile(
        record.name_normalized or "", config, record.id, signals(record, excluded_domains)
    )


def name_profile(
    name: str, config: FuzzyConfig, record_id: UUID = NO_ID, found: Signals = NO_SIGNALS
) -> Profile:
    tokens = name.split()
    generic = config.generic_tokens
    words = tuple(tokens[: config.max_name_words])
    return Profile(
        id=record_id,
        name=name,
        numbers=frozenset(token for token in tokens if any(char.isdigit() for char in token)),
        distinctive=tuple(token for token in words if token not in generic),
        words=words,
        block_words=frozenset(
            token
            for token in words
            if len(token) >= config.min_token_length
            and token not in generic
            and not token.isdigit()
        ),
        signals=found,
    )


def compare_signals(left: Signals, right: Signals) -> tuple[Fields, Sides]:
    """Signals both records share, and identifiers on which they disagree.

    Different phone numbers are not a conflict: one organization often has several.
    """
    agreeing: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str, str]] = []
    for field, ours, theirs, conflicting in (
        ("city", left.cities, right.cities, True),
        ("phone", left.phones, right.phones, False),
        ("email_domain", left.email_domains, right.email_domains, True),
    ):
        if ours & theirs:
            agreeing.append((field, min(ours & theirs)))
        elif ours and theirs and conflicting:
            conflicts.append((field, ",".join(sorted(ours)), ",".join(sorted(theirs))))
    for field, ours_one, theirs_one in (
        ("domain", left.domain, right.domain),
        ("linkedin", left.linkedin, right.linkedin),
    ):
        if ours_one and theirs_one and ours_one != theirs_one:
            conflicts.append((field, ours_one, theirs_one))
    return tuple(agreeing), tuple(conflicts)

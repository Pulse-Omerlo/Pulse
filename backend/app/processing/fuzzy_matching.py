# AI contribution: 50% or more AI-generated
"""Tier 2: propose organizations whose normalized names are alike but not equal."""

from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from rapidfuzz import fuzz

from app.models import SourceRecord
from app.processing.fuzzy_config import FuzzyConfig, load_fuzzy_config
from app.processing.normalization import email_domain

FUZZY_RULE_VERSION = "tier2-v1"
# Similar names plus an agreeing secondary signal and no conflicting one: may be linked.
FUZZY_RULE = "fuzzy"
# Similar names alone: only ever proposed for review.
FUZZY_NAME_RULE = "fuzzy_name"
FUZZY_RULES = frozenset({FUZZY_RULE, FUZZY_NAME_RULE})

Pair = tuple[UUID, UUID]
Key = tuple[str, ...]
# (field, value) found on both records.
Fields = tuple[tuple[str, str], ...]
# (field, value on the left record, value on the right record).
Sides = tuple[tuple[str, str, str], ...]
Scorer = Callable[[str, str], float]

# Measured against ratio, token_set_ratio and WRatio on the synthetic duplicates.
SCORER: Scorer = fuzz.token_sort_ratio


@dataclass(frozen=True)
class FuzzyMatch:
    left: UUID
    right: UUID
    rule: str
    score: float
    fields: Fields
    sides: Sides

    @property
    def pair(self) -> Pair:
        return (self.left, self.right)

    @property
    def strong(self) -> bool:
        return self.rule == FUZZY_RULE


@dataclass(frozen=True)
class Blocking:
    pairs: frozenset[Pair]
    # Blocks over max_block_size, left unexpanded.
    skipped_blocks: int


@dataclass(frozen=True)
class Signals:
    """What else, besides the name, can confirm or contradict a fuzzy match."""

    cities: frozenset[str]
    phones: frozenset[str]
    email_domains: frozenset[str]
    domain: str | None
    linkedin: str | None


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


def _numbers(tokens: Sequence[str]) -> frozenset[str]:
    return frozenset(token for token in tokens if any(char.isdigit() for char in token))


def _token_agreement(ours: Sequence[str], theirs: Sequence[str], generic: frozenset[str]) -> float:
    """How well each distinctive word of `ours` is spelled somewhere in `theirs` (worst word)."""
    distinctive = [token for token in ours if token not in generic]
    if not distinctive:
        return 0.0
    return min(max(fuzz.ratio(token, other) for other in theirs) for token in distinctive)


def similarity(left: str, right: str, generic: frozenset[str], scorer: Scorer = SCORER) -> float:
    """0 to 1: the whole-name score, capped by the worst-matched distinctive word.

    Names whose numbers differ, or made only of generic words, score 0.
    """
    left_tokens, right_tokens = left.split(), right.split()
    if _numbers(left_tokens) != _numbers(right_tokens):
        return 0.0
    words = min(
        _token_agreement(left_tokens, right_tokens, generic),
        _token_agreement(right_tokens, left_tokens, generic),
    )
    return min(scorer(left, right), words) / 100


def _block_tokens(record: SourceRecord, config: FuzzyConfig) -> set[str]:
    return {
        token
        for token in (record.name_normalized or "").split()
        if len(token) >= config.min_token_length
        and token not in config.generic_tokens
        and not token.isdigit()
    }


def _common_tokens(records: Sequence[SourceRecord], config: FuzzyConfig) -> frozenset[str]:
    counts = Counter(token for record in records for token in _block_tokens(record, config))
    limit = max(config.max_token_share * len(records), config.min_common_count)
    return frozenset(token for token, count in counts.items() if count > limit)


def block_keys(record: SourceRecord, config: FuzzyConfig, common: frozenset[str]) -> set[Key]:
    keys: set[Key] = {
        ("prefix", token[: config.prefix_length])
        for token in _block_tokens(record, config) - common
    }
    keys |= {("city", city) for city in signals(record, frozenset()).cities}
    return keys


def candidate_pairs(records: Sequence[SourceRecord], config: FuzzyConfig) -> Blocking:
    """Pairs of one tenant's records that share a block.

    At most blocks * max_block_size**2 / 2 pairs, instead of n**2 / 2 for every pair.
    """
    common = _common_tokens(records, config)
    blocks: dict[Key, list[UUID]] = defaultdict(list)
    for record in records:
        for key in block_keys(record, config, common):
            blocks[key].append(record.id)
    pairs: set[Pair] = set()
    skipped = 0
    for members in blocks.values():
        if len(members) > config.max_block_size:
            skipped += 1
            continue
        pairs.update(combinations(sorted(members), 2))
    return Blocking(frozenset(pairs), skipped)


def decide(
    left: SourceRecord,
    right: SourceRecord,
    score: float,
    excluded_domains: frozenset[str],
) -> FuzzyMatch:
    """A link only with an agreeing signal and no conflict; otherwise a review."""
    agreeing, conflicts = compare_signals(
        signals(left, excluded_domains), signals(right, excluded_domains)
    )
    rule = FUZZY_RULE if agreeing and not conflicts else FUZZY_NAME_RULE
    names = ("name_normalized", left.name_normalized or "", right.name_normalized or "")
    return FuzzyMatch(
        left=left.id,
        right=right.id,
        rule=rule,
        score=round(score, 3),
        fields=(("name_similarity", f"{score:.3f}"), *agreeing),
        sides=(names, *conflicts),
    )


def _tenant_matches(
    records: Sequence[SourceRecord],
    excluded_domains: frozenset[str],
    known_pairs: Collection[Pair],
    config: FuzzyConfig,
) -> list[FuzzyMatch]:
    by_id = {record.id: record for record in records}
    matches = []
    for pair in sorted(candidate_pairs(records, config).pairs):
        left, right = by_id[pair[0]], by_id[pair[1]]
        # Equal names are Tier 1's name rules.
        if pair in known_pairs or left.name_normalized == right.name_normalized:
            continue
        score = similarity(left.name_normalized, right.name_normalized, config.generic_tokens)
        if score >= config.threshold:
            matches.append(decide(left, right, score, excluded_domains))
    return matches


def fuzzy_matches(
    records: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
    known_pairs: Collection[Pair] = frozenset(),
    config: FuzzyConfig | None = None,
) -> list[FuzzyMatch]:
    """Tier 2 matches between current organization records, skipping known_pairs.

    known_pairs are ordered (smaller id first), like the pairs Tier 1 classified.
    """
    config = config or load_fuzzy_config()
    tenants: dict[UUID, list[SourceRecord]] = defaultdict(list)
    for record in records:
        if record.kind == "organization" and record.valid_to is None and record.name_normalized:
            tenants[record.tenant_id].append(record)
    matches = []
    for tenant_id in sorted(tenants):
        matches += _tenant_matches(tenants[tenant_id], excluded_domains, known_pairs, config)
    return matches

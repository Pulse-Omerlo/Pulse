# AI contribution: 50% or more AI-generated
"""Group records with Tier 1 then Tier 2, as the matcher does, for the dev-only report."""

import random
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.models import SourceRecord
from app.processing.cluster_groups import Identifiers, any_disagree
from app.processing.clustering import member_from_record
from app.processing.entity_resolution import blocked_matches, load_generic_domains
from app.processing.fuzzy_config import FuzzyConfig
from app.processing.fuzzy_matching import FuzzyMatch, fuzzy_matches

# Name tokens at least this long get the typo, so short words stay readable.
TYPO_MIN_TOKEN_LENGTH = 5


@dataclass(frozen=True)
class Grouping:
    """External ids grouped into organizations, and what happened to the Tier 2 pairs."""

    groups: list[set[str]] = field(default_factory=list)
    tier2_linked: int = 0
    tier2_review: int = 0


def _strong_components(matches: Iterable[FuzzyMatch]) -> list[tuple[set[UUID], list[FuzzyMatch]]]:
    """Connected records of the strong Tier 2 edges, with the edges of each."""
    parent: dict[UUID, UUID] = {}

    def find(node: UUID) -> UUID:
        while parent.setdefault(node, node) != node:
            node = parent[node]
        return node

    strong = [match for match in matches if match.strong]
    for match in strong:
        parent[find(match.left)] = find(match.right)
    groups: dict[UUID, tuple[set[UUID], list[FuzzyMatch]]] = {}
    for match in strong:
        members, edges = groups.setdefault(find(match.left), (set(), []))
        members.update(match.pair)
        edges.append(match)
    return [groups[root] for root in sorted(groups)]


def tier2_grouping(
    records: Sequence[SourceRecord], tier1_groups: Iterable[set[str]], config: FuzzyConfig
) -> Grouping:
    """Tier 1's groups of external ids, then Tier 2 edges that attach unlinked records or
    form new groups. A Tier 2 edge never joins two Tier 1 groups; it goes to review instead.
    """
    by_id = {record.id: record for record in records}
    by_external_id = {record.external_id: record.id for record in records}
    excluded = load_generic_domains()
    known = {match.providePair for match in blocked_matches(records, excluded)}
    groups = [{by_external_id[key] for key in group} for group in tier1_groups]
    owner = {record_id: index for index, group in enumerate(groups) for record_id in group}
    tier2 = fuzzy_matches(records, excluded, known, config)
    linked = review = 0

    for members, edges in _strong_components(tier2):
        touched = {owner[record_id] for record_id in members if record_id in owner}
        everyone = members.union(*(groups[index] for index in touched))
        found = [Identifiers.of(member_from_record(by_id[key], excluded)) for key in everyone]
        if len(touched) > 1 or any_disagree(found):
            review += len(edges)
            continue
        index = touched.pop() if touched else len(groups)
        if index == len(groups):
            groups.append(set())
        groups[index] |= members
        owner.update(dict.fromkeys(members, index))
        linked += len(edges)

    for match in tier2:
        same = match.left in owner and owner[match.left] == owner.get(match.right)
        if not match.strong and not same:
            review += 1
    return Grouping(
        groups=[{by_id[record_id].external_id for record_id in group} for group in groups],
        tier2_linked=linked,
        tier2_review=review,
    )


def _typo(name: str, rng: random.Random) -> str:
    """One deleted, swapped, replaced or added letter in a long word of the name."""
    tokens = name.split()
    long_tokens = [i for i, token in enumerate(tokens) if len(token) >= TYPO_MIN_TOKEN_LENGTH]
    longest = max(range(len(tokens)), key=lambda i: len(tokens[i]))
    index = rng.choice(long_tokens) if long_tokens else longest
    token = tokens[index]
    at = rng.randrange(1, len(token)) if len(token) > 1 else 0
    letter = rng.choice([c for c in string.ascii_lowercase if c != token[at]])
    edits = {
        "delete": token[:at] + token[at + 1 :],
        "swap": token[: at - 1] + token[at] + token[at - 1] + token[at + 1 :] if at else token,
        "replace": token[:at] + letter + token[at + 1 :],
        "insert": token[:at] + letter + token[at:],
    }
    edited = edits[rng.choice(sorted(edits))]
    # Swapping two equal letters changes nothing.
    tokens[index] = edited if edited != token else edits["replace"]
    return " ".join(tokens)


def _name_variant(name: str, generic: frozenset[str], rng: random.Random) -> str:
    """A typo, the first word moved to the end, or a generic word left out."""
    tokens = name.split()
    droppable = [i for i, token in enumerate(tokens) if token in generic]
    change = rng.choice(("typo", "typo", "reorder", "drop_generic"))
    if change == "reorder" and len(tokens) > 1:
        return " ".join([*tokens[1:], tokens[0]])
    if change == "drop_generic" and droppable and len(tokens) > 1:
        dropped = rng.choice(droppable)
        return " ".join(token for i, token in enumerate(tokens) if i != dropped)
    return _typo(name, rng)


def hard_variant(
    records: Sequence[SourceRecord], ground_truth: dict, seed: int, generic: frozenset[str]
) -> list[SourceRecord]:
    """Copies where each duplicate shares no identifier and its name is spelled differently.

    Tier 1 cannot find these duplicates, so the variant measures what Tier 2 adds.
    """
    second = {duplicate["record_ids"][1] for duplicate in ground_truth["organization_duplicates"]}
    result = []
    for record in records:
        copy = _copy(record)
        if record.external_id in second and record.name_normalized:
            rng = random.Random(f"{seed}:{record.external_id}")
            attributes = dict(record.attributes or {})
            identifiers = dict(attributes.get("identifiers") or {})
            identifiers.update(emails=[], domains=[], linkedin=[])
            copy.attributes = {**attributes, "identifiers": identifiers}
            copy.name_normalized = _name_variant(record.name_normalized, generic, rng)
            copy.domain = copy.linkedin = None
            copy.external_ids = {}
        result.append(copy)
    return result


def _copy(record: SourceRecord) -> SourceRecord:
    return SourceRecord(
        id=record.id,
        tenant_id=record.tenant_id,
        kind=record.kind,
        external_id=record.external_id,
        external_ids=record.external_ids,
        attributes=record.attributes,
        name_normalized=record.name_normalized,
        domain=record.domain,
        linkedin=record.linkedin,
        city=record.city,
    )

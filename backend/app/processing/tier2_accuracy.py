# AI contribution: 50% or more AI-generated
"""Group records with Tier 1 and Tier 2, as the matcher does, for the dev-only report."""

import random
import string
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.models import SourceRecord
from app.processing.clustering import member_from_record, plan_clusters
from app.processing.entity_resolution import blocked_matches, fuzzy_edges, load_generic_domains
from app.processing.fuzzy_config import FuzzyConfig

# Name tokens at least this long get the typo, so short words stay readable.
TYPO_MIN_TOKEN_LENGTH = 5


@dataclass(frozen=True)
class Grouping:
    """External ids grouped into organizations, and what happened to the Tier 2 pairs."""

    groups: list[set[str]] = field(default_factory=list)
    tier2_linked: int = 0
    tier2_review: int = 0


def tier2_grouping(records: Sequence[SourceRecord], config: FuzzyConfig) -> Grouping:
    """Plan Tier 1 and Tier 2 edges together, as match_organizations does."""
    by_id = {record.id: record for record in records}
    excluded = load_generic_domains()
    members = {record.id: member_from_record(record, excluded) for record in records}
    tier1 = blocked_matches(records, excluded)
    tier2 = fuzzy_edges(records, excluded, tier1, config)
    plan = plan_clusters(members, tier1 + tier2, {})
    cut = {edge.providePair for edge in plan.candidates}
    owner = {record_id: index for index, c in enumerate(plan.clusters) for record_id in c.members}
    linked = sum(edge.strong and edge.providePair not in cut for edge in tier2)
    review = sum(
        edge.providePair in cut
        or (not edge.strong and owner.get(edge.left, -1) != owner.get(edge.right, -2))
        for edge in tier2
    )
    return Grouping(
        groups=[{by_id[key].external_id for key in cluster.members} for cluster in plan.clusters],
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

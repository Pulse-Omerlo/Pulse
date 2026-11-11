# AI contribution: 50% or more AI-generated
"""Match rules, their weights and the edge type shared by matching and clustering."""

from dataclasses import dataclass
from uuid import UUID

# Version of the rules, stored with each decision.
RULE_VERSION = "tier1-v1"

# Strong rules from highest to lowest priority; a pair takes the first one that matches.
RULE_PRIORITIES = ("domain", "linkedin", "external_id", "name_city")

# Weight of each rule, used as a link's confidence and a candidate's score.
RULE_WEIGHTS = {
    "domain": 1.0,
    "linkedin": 1.0,
    "external_id": 1.0,
    "name_city": 0.95,
    "name_only": 0.5,
}

# Rules that only ever propose a pair for review.
WEAK_RULES = frozenset({"name_only"})

# Rules of the deterministic first tier.
TIER1_RULES = frozenset(RULE_PRIORITIES)

# Rules trusted to merge two existing organizations; a shared name and city is not enough.
MERGE_RULES = frozenset({"domain", "linkedin", "external_id"})

# Two record version ids.
Pair = tuple[UUID, UUID]
# (field, value) for each field a rule matched on.
Fields = tuple[tuple[str, str], ...]


# Two record versions and the rule that matched them.
@dataclass(frozen=True)
class MatchCandidateData:
    left: UUID
    right: UUID
    rule: str
    fields: Fields

    @property
    def providePair(self) -> Pair:
        """Return the pair of record version IDs in a consistent order."""
        return (self.left, self.right) if self.left < self.right else (self.right, self.left)

    # Strong rules may link records; weak ones only propose a pair for review.
    @property
    def strong(self) -> bool:
        return self.rule not in WEAK_RULES


def rule_weight(rule: str) -> float:
    """Weight of a rule, 0 for a rule without one."""
    return RULE_WEIGHTS.get(rule, 0.0)


def strength_key(edge: MatchCandidateData) -> tuple[int, float, UUID, UUID]:
    """Sort key putting the strongest edge first: Tier 1 priority, then weight, then pair."""
    rank = RULE_PRIORITIES.index(edge.rule) if edge.rule in TIER1_RULES else len(RULE_PRIORITIES)
    return (rank, -rule_weight(edge.rule), *edge.providePair)


def can_merge_organizations(edge: MatchCandidateData) -> bool:
    """Only an edge on a shared identifier may join two existing organizations."""
    return edge.rule in MERGE_RULES

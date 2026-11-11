# AI contribution: 50% or more AI-generated
"""Match rules, their weights and the edge type shared by matching and clustering."""

from dataclasses import dataclass
from uuid import UUID

# Naming the version of the matching rules used to make a decision
# If we need to change matching behavior later, we can create a new version and keep the old one for
# reference
RULE_VERSION = "tier1-v1"

# Priority of strong matching rules (Left = Highest priotity, Right = Lowest priority)
# Uses the highest priority rule that matches to make a decision, and ignores any lower priority
# rules that also match
RULE_PRIORITIES = ("domain", "linkedin", "external_id", "name_city")

# Dictionnary assigning a weight to each matching rule, used to compute a score for each match
# candidate
RULE_WEIGHTS = {
    "domain": 1.0,
    "linkedin": 1.0,
    "external_id": 1.0,
    "name_city": 0.95,
    "name_only": 0.5,
}

# Rules that only ever propose a pair for review.
WEAK_RULES = frozenset({"name_only"})

# Rules trusted to merge two existing organizations.
TIER1_RULES = frozenset(RULE_PRIORITIES)

# Tupple containing the two record version IDs
Pair = tuple[UUID, UUID]
# Inner tupples containing the field name and value for each matching field
Fields = tuple[tuple[str, str], ...]


# Class for a match candidate, containing the two record version IDs and the matching rule that
# matched them
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

    # Distinguish rules that are strong enough to be used for automatic resolution from those that
    # are not
    @property
    def strong(self) -> bool:
        return self.rule not in WEAK_RULES


def rule_weight(rule: str) -> float:
    """Weight of a rule, 0 for a rule without one."""
    return RULE_WEIGHTS.get(rule, 0.0)


def strength_key(edge: MatchCandidateData) -> tuple:
    """Sort key putting the strongest edge first: Tier 1 priority, then weight, then pair."""
    rank = RULE_PRIORITIES.index(edge.rule) if edge.rule in TIER1_RULES else len(RULE_PRIORITIES)
    return (rank, -rule_weight(edge.rule), *edge.providePair)


def can_merge_organizations(edge: MatchCandidateData) -> bool:
    """Only a Tier 1 edge may join two existing organizations."""
    return edge.rule in TIER1_RULES

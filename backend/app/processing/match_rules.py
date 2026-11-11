# AI contribution: 50% or more AI-generated
"""Match rules, their weights and the edge type shared by matching and clustering."""

from dataclasses import dataclass
from uuid import UUID

# Version of the rules, stored with each decision.
RULE_VERSION = "tier1-v1"

# Strong rules from highest to lowest priority; a pair takes the first one that matches.
RULE_PRIORITIES = ("domain", "linkedin", "external_id", "name_city")

# Tier 2 rules, versioned on their own.
FUZZY_RULE_VERSION = "tier2-v1"
# Similar names with enough agreeing evidence and no conflict: may be linked.
FUZZY_RULE = "fuzzy"
# Similar names without enough evidence: only ever proposed for review.
FUZZY_NAME_RULE = "fuzzy_name"
FUZZY_RULES = frozenset({FUZZY_RULE, FUZZY_NAME_RULE})

# Weight of each rule, used as a link's confidence and a candidate's score.
RULE_WEIGHTS = {
    "domain": 1.0,
    "linkedin": 1.0,
    "external_id": 1.0,
    "name_city": 0.95,
    "name_only": 0.5,
    # Scaled by the name similarity, so a fuzzy link stays below every Tier 1 rule.
    FUZZY_RULE: 0.9,
    FUZZY_NAME_RULE: 0.5,
}

# Rules that only ever propose a pair for review.
WEAK_RULES = frozenset({"name_only", FUZZY_NAME_RULE})

# Rules of the deterministic first tier.
TIER1_RULES = frozenset(RULE_PRIORITIES)

# Rules trusted to merge two existing organizations; a shared name and city is not enough.
MERGE_RULES = frozenset({"domain", "linkedin", "external_id"})

# Two record version ids.
Pair = tuple[UUID, UUID]
# (field, value) for each field a rule matched on.
Fields = tuple[tuple[str, str], ...]
# Field name, value on the left record, value on the right record
Sides = tuple[tuple[str, str, str], ...]


# Two record versions and the rule that matched them.
@dataclass(frozen=True)
class MatchCandidateData:
    left: UUID
    right: UUID
    rule: str
    fields: Fields
    # Name similarity behind a fuzzy rule; Tier 1 rules use their weight instead.
    score: float | None = None
    # Fields whose values differ between the two records, such as fuzzy names.
    sides: Sides = ()

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


def edge_strength(edge: MatchCandidateData) -> float:
    """The rule weight, scaled by the similarity when the rule has one."""
    similarity = 1.0 if edge.score is None else edge.score
    return rule_weight(edge.rule) * similarity


def link_confidence(edge: MatchCandidateData) -> float:
    """The edge strength as stored on a link, to three decimals."""
    return round(edge_strength(edge), 3)


def candidate_score(edge: MatchCandidateData) -> float:
    """Comparable across rules, so a fuzzy candidate never ranks above a Tier 1 one."""
    return link_confidence(edge)


def rule_set_version(rule: str) -> str:
    return FUZZY_RULE_VERSION if rule in FUZZY_RULES else RULE_VERSION


def decided_by(edge: MatchCandidateData) -> str:
    tier = "tier2" if edge.rule in FUZZY_RULES else "tier1"
    return f"{tier}:{rule_set_version(edge.rule)}"


def strength_key(edge: MatchCandidateData) -> tuple[int, float, UUID, UUID]:
    """Sort key putting the strongest edge first: Tier 1 priority, then strength, then pair.

    The strength is not rounded, so two different similarities never tie.
    """
    rank = RULE_PRIORITIES.index(edge.rule) if edge.rule in TIER1_RULES else len(RULE_PRIORITIES)
    return (rank, -edge_strength(edge), *edge.providePair)


def can_merge_organizations(edge: MatchCandidateData) -> bool:
    """Only an edge on a shared identifier may join two existing organizations."""
    return edge.rule in MERGE_RULES

# AI contribution: 50% or more AI-generated
from uuid import UUID

from app.processing import match_rules
from app.processing.match_rules import MatchCandidateData

LEFT, RIGHT = UUID(int=1), UUID(int=2)


def edge(rule, left=LEFT, right=RIGHT):
    return MatchCandidateData(left, right, rule, ())


def test_only_weak_rules_are_not_strong():
    # Act / Assert
    rules = ("name_only", "fuzzy_name", "domain", "fuzzy")
    assert [edge(rule).strong for rule in rules] == [False, False, True, True]


def test_unknown_rules_sort_after_tier1_by_weight(monkeypatch):
    # Arrange
    monkeypatch.setitem(match_rules.RULE_WEIGHTS, "fuzzy", 0.8)
    edges = [edge("fuzzy_name"), edge("fuzzy"), edge("name_city"), edge("domain")]
    # Act
    ordered = sorted(edges, key=match_rules.strength_key)
    # Assert
    assert [item.rule for item in ordered] == ["domain", "name_city", "fuzzy", "fuzzy_name"]


def test_same_rule_sorts_by_pair_whatever_the_side():
    # Arrange
    later = edge("domain", UUID(int=9), UUID(int=3))
    earlier = edge("domain", UUID(int=4), UUID(int=2))
    # Act
    ordered = sorted([later, earlier], key=match_rules.strength_key)
    # Assert
    assert ordered == [earlier, later]


def test_only_identifier_rules_can_merge_organizations():
    # Act
    allowed = [
        rule
        for rule in ("domain", "linkedin", "external_id", "name_city", "fuzzy")
        if match_rules.can_merge_organizations(edge(rule))
    ]
    # Assert
    assert allowed == ["domain", "linkedin", "external_id"]
    assert match_rules.rule_weight("unknown") == 0.0


def fuzzy(score, left=LEFT, right=RIGHT, rule="fuzzy"):
    return MatchCandidateData(left, right, rule, (), score)


def test_more_similar_fuzzy_edges_sort_first():
    # Arrange
    edges = [fuzzy(0.88, UUID(int=1), UUID(int=2)), fuzzy(0.95, UUID(int=3), UUID(int=4))]
    # Act
    ordered = sorted([*edges, edge("name_city")], key=match_rules.strength_key)
    # Assert
    assert [item.score for item in ordered] == [None, 0.95, 0.88]


def test_fuzzy_link_confidence_stays_below_every_tier1_rule():
    # Arrange
    tier1 = [match_rules.RULE_WEIGHTS[rule] for rule in match_rules.TIER1_RULES]
    # Act
    perfect = match_rules.link_confidence(fuzzy(1.0))
    usual = match_rules.link_confidence(fuzzy(0.875))
    # Assert
    assert perfect < min(tier1)
    assert usual == round(0.9 * 0.875, 3)
    assert match_rules.link_confidence(edge("domain")) == 1.0


def test_candidate_score_is_the_similarity_or_the_rule_weight():
    # Act / Assert
    assert match_rules.candidate_score(fuzzy(0.91, rule="fuzzy_name")) == 0.91
    assert match_rules.candidate_score(edge("name_only")) == 0.5


def test_decisions_name_their_tier_and_rule_version():
    # Act / Assert
    assert match_rules.decided_by(fuzzy(0.9)) == "tier2:tier2-v1"
    assert match_rules.decided_by(edge("domain")) == "tier1:tier1-v1"
    assert match_rules.rule_set_version("fuzzy_name") == "tier2-v1"
    assert match_rules.rule_set_version("name_city") == "tier1-v1"

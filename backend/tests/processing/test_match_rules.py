# AI contribution: 50% or more AI-generated
from uuid import UUID

from app.processing import match_rules
from app.processing.match_rules import MatchCandidateData

LEFT, RIGHT = UUID(int=1), UUID(int=2)


def edge(rule, left=LEFT, right=RIGHT):
    return MatchCandidateData(left, right, rule, ())


def test_only_weak_rules_are_not_strong():
    # Act / Assert
    assert [edge(rule).strong for rule in ("name_only", "domain", "fuzzy")] == [False, True, True]


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
    assert match_rules.rule_weight("fuzzy") == 0.0

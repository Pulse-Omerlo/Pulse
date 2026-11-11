# AI contribution: 50% or more AI-generated
"""Tier 2 edges and their evidence in the matcher. No database."""

from uuid import UUID

from app.models import SourceRecord
from app.processing import entity_resolution as er

LEFT, RIGHT = UUID(int=1), UUID(int=2)


def record(number, name, **fields):
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": UUID(int=100),
                "kind": "organization",
                "external_id": str(number),
                "external_ids": {},
                "attributes": {},
                "name_normalized": name,
                "city": "quebec",
            }
            | fields
        )
    )


def fuzzy(score=0.875, rule="fuzzy"):
    return er.MatchCandidateData(
        LEFT,
        RIGHT,
        rule,
        (("name_similarity", f"{score:.3f}"), ("city", "quebec")),
        score,
        (("name_normalized", "boulangerie tremblay", "boulangerie tremblai"),),
    )


def test_fuzzy_evidence_shows_each_record_its_own_name_first():
    # Act
    left_view = er.evidence(fuzzy(), RIGHT)
    right_view = er.evidence(fuzzy(), LEFT)
    # Assert
    assert [entry["field"] for entry in left_view] == ["name_similarity", "city", "name_normalized"]
    assert {entry["rule_set_version"] for entry in left_view} == {"tier2-v1"}
    assert (left_view[2]["value"], left_view[2]["matched_value"]) == (
        "boulangerie tremblay",
        "boulangerie tremblai",
    )
    assert (right_view[2]["value"], right_view[2]["matched_value"]) == (
        "boulangerie tremblai",
        "boulangerie tremblay",
    )
    assert left_view[1]["value"] == left_view[1]["matched_value"] == "quebec"


def test_tier1_evidence_is_unchanged():
    # Arrange
    match = er.MatchCandidateData(LEFT, RIGHT, "domain", (("domain", "a.ca"),))
    # Act
    [entry] = er.evidence(match, RIGHT)
    # Assert
    assert entry == {
        "rule": "domain",
        "rule_set_version": "tier1-v1",
        "field": "domain",
        "value": "a.ca",
        "matched_value": "a.ca",
        "matched_record_id": str(RIGHT),
    }


def test_fuzzy_edges_skip_pairs_tier1_classified():
    # Arrange
    records = [
        record(1, "imprimerie belanger"),
        record(2, "imprimerie belangers"),
        record(3, "imprimerie belangere"),
    ]
    tier1 = [er.MatchCandidateData(UUID(int=1), UUID(int=2), "domain", ())]
    # Act
    edges = er.fuzzy_edges(records, frozenset(), tier1)
    # Assert
    assert {edge.providePair for edge in edges} == {
        (UUID(int=1), UUID(int=3)),
        (UUID(int=2), UUID(int=3)),
    }
    assert all(edge.rule == "fuzzy" and edge.score for edge in edges)

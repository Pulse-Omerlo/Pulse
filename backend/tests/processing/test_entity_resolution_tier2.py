# AI contribution: 50% or more AI-generated
"""Tier 2 edges and their evidence in the matcher. No database."""

import json
from uuid import UUID

import pytest

from app.core.logging import configure_logging
from app.models import OrgLink
from app.processing import entity_resolution as er
from app.processing.fuzzy_blocking import BlockingStats
from app.processing.fuzzy_matching import FuzzyResult

LEFT, RIGHT = UUID(int=1), UUID(int=2)


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


def test_tier2_work_and_what_it_left_out_are_logged(capsys):
    # Arrange
    configure_logging()
    result = FuzzyResult((fuzzy(),), comparisons=12, blocking=BlockingStats(9, 2, 1, 40))
    # Act
    er.log_tier2(UUID(int=100), result)
    [event] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # Assert
    assert event["event"] == "tier2_matching"
    assert (event["matches"], event["comparisons"]) == (1, 12)
    assert (event["split_blocks"], event["windowed_blocks"], event["pairs_left_out"]) == (2, 1, 40)


def test_a_link_placed_through_a_fuzzy_match_carries_a_marker():
    # Act
    marked = er.evidence(fuzzy(), RIGHT, tentative=True)
    plain = er.evidence(fuzzy(), RIGHT)
    # Assert
    assert all(entry["tentative"] is True for entry in marked)
    assert all("tentative" not in entry for entry in plain)


@pytest.mark.parametrize(
    "method,evidence,tentative",
    [
        ("fuzzy", [], True),
        ("domain", [{"field": "domain", "tentative": True}], True),
        ("domain", [{"field": "domain"}], False),
        ("manual", [{"decided_by": "reviewer"}], False),
        ("domain", None, False),
    ],
)
def test_stored_links_are_tentative_by_method_or_marker(method, evidence, tentative):
    # Arrange
    link = OrgLink(method=method, evidence=evidence)
    # Act / Assert
    assert er.is_tentative(link) is tentative

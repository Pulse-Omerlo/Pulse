# AI contribution: 50% or more AI-generated
"""Tests for the accuracy scoring and report."""

import json
from uuid import UUID

import pytest

from app import tier1_report
from app.models import SourceRecord
from app.processing.tier1_accuracy import (
    combine,
    expected_pairs,
    f1,
    precision,
    predicted_pairs,
    recall,
    score,
)


def test_expected_pairs_come_from_organization_duplicates():
    # Arrange
    ground_truth = {
        "organization_duplicates": [
            {"entity": 1, "record_ids": ["b", "a"], "changes": ["case"]},
            {"entity": 2, "record_ids": ["c", "d"], "changes": ["suffix"]},
        ],
        "person_duplicates": [{"person": 5, "record_ids": ["p1", "p2"], "changes": []}],
    }
    # Act
    pairs = expected_pairs(ground_truth)
    # Assert
    assert pairs == {("a", "b"), ("c", "d")}


def test_expected_pairs_can_read_person_duplicates():
    # Arrange
    ground_truth = {
        "organization_duplicates": [
            {"entity": 1, "record_ids": ["a", "b"], "changes": []},
        ],
        "person_duplicates": [{"person": 5, "record_ids": ["p2", "p1"], "changes": []}],
    }

    # Act / Assert
    assert expected_pairs(ground_truth, key="person_duplicates") == {("p1", "p2")}


def test_expected_pairs_is_empty_without_duplicates():
    # Act / Assert
    assert expected_pairs({}) == set()
    assert expected_pairs({}, key="person_duplicates") == set()


def test_predicted_pairs_lists_every_pair_in_a_group():
    # Arrange
    groups = [{"a", "b", "c"}, {"d", "e"}]
    # Act
    pairs = predicted_pairs(groups)
    # Assert
    assert pairs == {("a", "b"), ("a", "c"), ("b", "c"), ("d", "e")}


def test_metrics_for_a_simple_case():
    # Act
    precision_value = precision(3, 1)
    recall_value = recall(3, 3)
    # Assert
    assert precision_value == 0.75
    assert recall_value == 0.5
    assert f1(precision_value, recall_value) == pytest.approx(0.6)


def test_metrics_are_zero_instead_of_dividing_by_zero():
    # Act / Assert
    assert precision(0, 0) == 0.0
    assert recall(0, 0) == 0.0
    assert f1(0.0, 0.0) == 0.0


def test_score_lists_false_positives_and_false_negatives():
    # Arrange
    expected = {("a", "b"), ("c", "d")}
    predicted = {("a", "b"), ("e", "f")}
    # Act
    result = score(expected, predicted)
    # Assert
    assert result["true_positives"] == 1
    assert result["false_positive_pairs"] == [("e", "f")]
    assert result["false_negative_pairs"] == [("c", "d")]
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5


def test_score_with_no_predictions_has_zero_precision():
    # Act
    result = score({("a", "b")}, set())
    # Assert
    assert result["precision"] == 0.0
    assert result["recall"] == 0.0
    assert result["false_negatives"] == 1


def test_combine_adds_counts_before_computing_metrics():
    # Arrange
    first = score({("a", "b")}, {("a", "b")})
    second = score({("c", "d"), ("e", "f")}, {("x", "y")})
    # Act
    total = combine([first, second])
    # Assert
    assert total["true_positives"] == 1
    assert total["false_positives"] == 1
    assert total["false_negatives"] == 2
    assert total["precision"] == 0.5
    assert total["recall"] == pytest.approx(1 / 3)


def test_report_logs_the_dev_only_notice_and_each_tenant(capsys):
    # Act
    tier1_report.main([])
    lines = capsys.readouterr().out.splitlines()
    events = [json.loads(line) for line in lines]
    # Assert
    assert events[0]["event"] == "tier1_report_started"
    assert events[0]["notice"] == tier1_report.HEADER
    org_scores = [
        event
        for event in events
        if event["event"] == "tier1_score" and event.get("kind") == "organization"
    ]
    assert [event["tenant"] for event in org_scores] == ["demo-capitale", "demo-estrie", "all"]
    both = [event for event in events if event["event"] == "tier2_score"]
    assert [event["tenant"] for event in both] == [event["tenant"] for event in org_scores]
    assert {"linked", "review"} <= both[-1].keys()
    people_scores = [
        event
        for event in events
        if event["event"] == "tier1_score" and event.get("kind") == "person"
    ]
    assert [event["tenant"] for event in people_scores] == [
        "demo-capitale",
        "demo-estrie",
        "all",
    ]


def test_hard_variant_shows_what_tier2_adds(capsys):
    # Act
    tier1_report.main(["--hard"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # Assert
    totals = {
        e["event"]: e
        for e in events
        if e.get("tenant") == "all" and e.get("kind") == "organization"
    }
    assert events[0]["variant"] == "hard"
    assert totals["tier1_score"]["recall"] == 0.0
    assert totals["tier2_score"]["recall"] > 0.5
    # The near-miss negatives add no false positive.
    assert totals["tier2_score"]["precision"] == 1.0
    assert totals["tier2_score"]["linked"] > 0 and totals["tier2_score"]["review"] > 0
    names = {e["event"] for e in events}
    # Tier 1 misses every hard duplicate, and the report lists each of them.
    assert {"tier1_false_negative", "tier2_false_negative"} <= names


def test_sweep_scores_each_threshold(capsys):
    # Act
    tier1_report.main(["--sweep"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # Assert
    thresholds = [e["threshold"] for e in events if e["event"] == "tier2_threshold"]
    assert thresholds == list(tier1_report.SWEEP_THRESHOLDS)


def test_report_logs_each_wrong_pair_as_a_warning(capsys):
    # Arrange
    tier1_report.configure_logging()
    result = score({("a", "b")}, {("c", "d")})
    # Act
    tier1_report.log_wrong_pairs("demo-capitale", result)
    tier1_report.log_wrong_pairs("demo-capitale", result, "tier2")
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # Assert
    assert [event["event"] for event in events] == [
        "tier1_false_positive",
        "tier1_false_negative",
        "tier2_false_positive",
        "tier2_false_negative",
    ]
    assert events[0]["level"] == "warning"
    assert (events[0]["left"], events[0]["right"]) == ("c", "d")


def organization(number, tenant_number, external_id):
    return SourceRecord(
        id=UUID(int=number),
        tenant_id=UUID(int=tenant_number),
        kind="organization",
        external_id=external_id,
        external_ids={},
        attributes={},
        domain="leblanc.example",
    )


def test_report_groups_a_shared_domain_pair():
    # Arrange
    records = [organization(1, 1, "org-1"), organization(2, 1, "org-2")]
    # Act
    groups = tier1_report.tier1_groups(records)
    # Assert
    assert groups == [{"org-1", "org-2"}]


def test_report_never_groups_records_from_two_tenants():
    # Arrange: the same organization exists in both tenants with the same domain.
    records = [organization(1, 1, "capitale-org"), organization(2, 2, "estrie-org")]
    # Act
    groups = tier1_report.tier1_groups(records)
    # Assert
    assert groups == []

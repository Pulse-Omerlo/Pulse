# AI contribution: 50% or more AI-generated
"""Tests for the Tier 1 accuracy scoring and report."""

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


def test_expected_pairs_is_empty_without_duplicates():
    # Act / Assert
    assert expected_pairs({}) == set()


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


def test_report_prints_the_dev_only_header_and_each_tenant(capsys):
    # Act
    tier1_report.main([])
    output = capsys.readouterr().out
    # Assert
    assert output.startswith(tier1_report.HEADER)
    assert "Tenant demo-capitale" in output
    assert "Tenant demo-estrie" in output
    assert "All tenants" in output


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

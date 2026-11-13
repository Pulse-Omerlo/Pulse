# AI contribution: 50% or more AI-generated
"""Tests for the Splink matching spike and its comparison report."""

import json
from uuid import UUID

import pytest

from app import splink_report
from app.processing import splink_matching
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.splink_matching import MATCH_FIELDS, prior, splink_pairs, splink_rows
from app.processing.tier1_accuracy import score
from app.processing.tier2_accuracy import organization_record
from app.tier1_report import tenant_datasets
from synthetic.omerlo_fixtures import DEFAULT_SEED


@pytest.fixture(scope="module")
def capitale():
    """Records and expected pairs of the first synthetic tenant."""
    _, records, expected = tenant_datasets(DEFAULT_SEED, False, load_fuzzy_config())[0]
    return records, expected


def test_rows_keep_the_match_fields_and_turn_blanks_into_none():
    # Arrange
    record = organization_record(
        UUID(int=1),
        UUID(int=7),
        external_id="org-1",
        name_normalized="groupe exemple",
        city="quebec",
        domain="",
        linkedin=None,
    )
    # Act
    rows = splink_rows([record])
    # Assert
    assert rows == [
        {
            "unique_id": "org-1",
            "tenant_id": str(UUID(int=7)),
            "name_normalized": "groupe exemple",
            "city": "quebec",
            "domain": None,
            "linkedin": None,
        }
    ]


def test_prior_spreads_the_duplicate_share_over_all_pairs():
    # Act / Assert: 10% of 101 records are duplicates, over 101 * 100 / 2 pairs.
    assert prior(101) == pytest.approx(0.002)


def test_no_records_and_one_record_give_no_pairs():
    # Arrange
    record = organization_record(UUID(int=1), UUID(int=1), external_id="org-1")
    # Act / Assert
    assert splink_pairs([]) == set()
    assert splink_pairs([record]) == set()


def test_splink_finds_the_synthetic_duplicates(capitale):
    # Arrange
    records, expected = capitale
    # Act
    result = score(expected, splink_pairs(records))
    # Assert
    assert result["f1"] >= 0.9
    assert result["false_positives"] == 0


def test_a_higher_threshold_only_drops_pairs(capitale):
    # Arrange
    records, _ = capitale
    # Act
    loose = splink_pairs(records, threshold=0.5)
    strict = splink_pairs(records, threshold=0.99)
    # Assert
    assert strict <= loose
    assert len(strict) < len(loose)


def copies_in_another_tenant(records):
    """The same records, unchanged, in a second tenant."""
    return [
        organization_record(
            UUID(int=number),
            UUID(int=99),
            external_id=f"copy-{record.external_id}",
            **{field: getattr(record, field) for field in MATCH_FIELDS},
        )
        for number, record in enumerate(records, start=1)
    ]


def test_records_from_two_tenants_never_match(capitale):
    # Arrange: every record also exists, unchanged, in a second tenant.
    records, _ = capitale
    copies = copies_in_another_tenant(records)
    tenant_of = {record.external_id: record.tenant_id for record in records + copies}
    # Act
    pairs = splink_pairs(records + copies)
    # Assert
    assert pairs
    assert all(tenant_of[left] == tenant_of[right] for left, right in pairs)


def test_each_tenant_trains_its_own_model(capitale, mocker):
    # Arrange
    records, _ = capitale
    model_input = mocker.spy(splink_matching, "splink_rows")
    # Act
    splink_pairs(records + copies_in_another_tenant(records))
    # Assert: one model per tenant, each built only from that tenant's records.
    tenants_per_model = [
        {record.tenant_id for record in call.args[0]} for call in model_input.call_args_list
    ]
    assert tenants_per_model == [{records[0].tenant_id}, {UUID(int=99)}]


def test_report_scores_each_method_per_tenant_and_in_total(capsys):
    # Act
    splink_report.main([])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # Assert
    assert events[0]["event"] == "splink_report_started"
    assert events[0]["threshold"] == 0.9
    for method in splink_report.METHODS:
        tenants = [e["tenant"] for e in events if e["event"] == f"{method}_score"]
        assert tenants == ["demo-capitale", "demo-estrie", "all"]
    splink_total = next(e for e in events if e["event"] == "splink_score" and e["tenant"] == "all")
    assert splink_total["f1"] >= 0.9


@pytest.mark.parametrize("threshold", ["0", "1.5", "-0.2"])
def test_report_rejects_a_threshold_outside_zero_to_one(threshold):
    # Act / Assert
    with pytest.raises(SystemExit):
        splink_report.main(["--threshold", threshold])

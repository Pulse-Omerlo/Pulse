# AI contribution: 50% or more AI-generated
"""Tier 1 plus Tier 2 grouping and the hard synthetic variant used by the report."""

import random
from uuid import UUID

from app import tier1_report
from app.models import SourceRecord
from app.processing import tier2_accuracy as accuracy
from app.processing.fuzzy_config import load_fuzzy_config

CONFIG = load_fuzzy_config()


def record(number, name, city="quebec", **fields):
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": UUID(int=1),
                "kind": "organization",
                "external_id": f"org-{number}",
                "external_ids": {},
                "attributes": {"cities": [city]},
                "name_normalized": name,
                "city": city,
            }
            | fields
        )
    )


def test_tier2_attaches_an_unlinked_record_to_a_tier1_group():
    # Arrange
    records = [
        record(1, "boulangerie tremblay", domain="tremblay.ca"),
        record(2, "boulangerie tremblay", domain="tremblay.ca"),
        record(3, "boulangerie tremblai"),
    ]
    # Act
    grouping = accuracy.tier2_grouping(records, tier1_report.tier1_groups(records), CONFIG)
    # Assert
    assert grouping.groups == [{"org-1", "org-2", "org-3"}]
    assert (grouping.tier2_linked, grouping.tier2_review) == (2, 0)


def test_tier2_never_joins_two_tier1_groups():
    # Arrange
    records = [
        record(1, "imprimerie belanger", domain="a.ca"),
        record(2, "imprimerie belanger x", domain="a.ca"),
        record(3, "imprimerie belager", linkedin="company/b"),
        record(4, "imprimerie belager z", linkedin="company/b"),
    ]
    # Act
    grouping = accuracy.tier2_grouping(records, tier1_report.tier1_groups(records), CONFIG)
    # Assert
    assert sorted(map(sorted, grouping.groups)) == [["org-1", "org-2"], ["org-3", "org-4"]]
    assert grouping.tier2_linked == 0 and grouping.tier2_review > 0


def test_tier2_pairs_without_a_signal_go_to_review():
    # Arrange
    records = [record(1, "boulangerie tremblay"), record(2, "boulangerie tremblai", city="levis")]
    # Act
    grouping = accuracy.tier2_grouping(records, tier1_report.tier1_groups(records), CONFIG)
    # Assert
    assert grouping.groups == []
    assert (grouping.tier2_linked, grouping.tier2_review) == (0, 1)


def test_tier2_edges_form_a_new_group():
    # Arrange
    records = [record(1, "boulangerie tremblay"), record(2, "boulangerie tremblai")]
    # Act
    grouping = accuracy.tier2_grouping(records, tier1_report.tier1_groups(records), CONFIG)
    # Assert
    assert grouping.groups == [{"org-1", "org-2"}]
    assert grouping.tier2_linked == 1


def test_hard_variant_respells_the_duplicate_and_drops_its_identifiers():
    # Arrange
    original = record(1, "boulangerie tremblay", domain="tremblay.ca")
    duplicate = record(
        2,
        "boulangerie tremblay",
        domain="tremblay.ca",
        linkedin="company/tremblay",
        external_ids={"crm_id": ["CRM-1"]},
        attributes={"identifiers": {"emails": ["info@tremblay.ca"], "phones": ["+1"]}},
    )
    truth = {"organization_duplicates": [{"record_ids": ["org-1", "org-2"]}]}
    # Act
    first, second = accuracy.hard_variant([original, duplicate], truth, 7, CONFIG.generic_tokens)
    again = accuracy.hard_variant([original, duplicate], truth, 7, CONFIG.generic_tokens)[1]
    # Assert
    assert first.name_normalized == "boulangerie tremblay" and first.domain == "tremblay.ca"
    assert second.name_normalized != "boulangerie tremblay"
    assert second.name_normalized == again.name_normalized
    assert (second.domain, second.linkedin, second.external_ids) == (None, None, {})
    assert second.attributes["identifiers"] == {
        "emails": [],
        "phones": ["+1"],
        "domains": [],
        "linkedin": [],
    }
    assert duplicate.domain == "tremblay.ca"


def test_name_variants_change_the_name():
    # Arrange
    names = ["boulangerie tremblay", "garage roy and fils", "lll", "ab", "x"]
    # Act
    variants = {
        (name, seed): accuracy._name_variant(name, CONFIG.generic_tokens, random.Random(seed))
        for name in names
        for seed in range(30)
    }
    # Assert
    assert all(variant != name for (name, _), variant in variants.items())

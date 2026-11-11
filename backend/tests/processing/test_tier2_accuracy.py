# AI contribution: 50% or more AI-generated
"""Tier 1 and Tier 1 plus Tier 2 groupings used by the report."""

from uuid import UUID

from app.processing import tier2_accuracy as accuracy
from app.processing.fuzzy_config import load_fuzzy_config

CONFIG = load_fuzzy_config()
PHONE = "+14185550001"


def record(number, name, city="quebec", **fields):
    defaults = {
        "external_id": f"org-{number}",
        "external_ids": {},
        # The same phone, so a spelling difference may link.
        "attributes": {"cities": [city], "identifiers": {"phones": [PHONE]}},
        "name_normalized": name,
        "city": city,
    }
    return accuracy.organization_record(UUID(int=number), UUID(int=1), **(defaults | fields))


def test_tier2_attaches_an_unlinked_record_to_a_tier1_group():
    # Arrange
    records = [
        record(1, "boulangerie tremblay", domain="tremblay.ca"),
        record(2, "boulangerie tremblay", domain="tremblay.ca"),
        record(3, "boulangerie tremblai"),
    ]
    # Act
    tier1, both = accuracy.tier_groupings(records, CONFIG)
    # Assert
    assert tier1.groups == (frozenset({"org-1", "org-2"}),)
    assert both.groups == (frozenset({"org-1", "org-2", "org-3"}),)
    # Only org-3 is linked through a fuzzy edge.
    assert (both.tier2_linked, both.tier2_review) == (1, 0)


def test_tier2_pairs_without_enough_evidence_go_to_review():
    # Arrange
    records = [record(1, "boulangerie tremblay"), record(2, "boulangerie tremblai", city="levis")]
    # Act
    _, both = accuracy.tier_groupings(records, CONFIG)
    # Assert
    assert both.groups == ()
    assert (both.tier2_linked, both.tier2_review) == (0, 1)


def test_tier2_linked_counts_the_records_it_links():
    # Arrange
    records = [record(1, "boulangerie tremblay"), record(2, "boulangerie tremblai")]
    # Act
    _, both = accuracy.tier_groupings(records, CONFIG)
    # Assert
    # A new organization of two records: both are saved with a fuzzy link.
    assert both.groups == (frozenset({"org-1", "org-2"}),)
    assert both.tier2_linked == 2


def test_copy_record_changes_only_what_it_is_given():
    # Arrange
    original = record(1, "boulangerie tremblay", domain="tremblay.ca")
    # Act
    copy = accuracy.copy_record(original, domain=None)
    # Assert
    assert (copy.id, copy.name_normalized, copy.domain) == (
        original.id,
        original.name_normalized,
        None,
    )
    assert original.domain == "tremblay.ca"


def test_tier2_never_joins_two_tier1_groups():
    # Arrange
    records = [
        record(1, "imprimerie belanger", domain="a.ca"),
        record(2, "imprimerie belanger x", domain="a.ca"),
        record(3, "imprimerie belager", linkedin="company/b"),
        record(4, "imprimerie belager z", linkedin="company/b"),
    ]
    # Act
    _, both = accuracy.tier_groupings(records, CONFIG)
    # Assert
    assert sorted(map(sorted, both.groups)) == [["org-1", "org-2"], ["org-3", "org-4"]]
    assert both.tier2_linked == 0 and both.tier2_review > 0

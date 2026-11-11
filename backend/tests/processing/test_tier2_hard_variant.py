# AI contribution: 50% or more AI-generated
"""The harder synthetic copy used by the dev-only report."""

import random
from uuid import UUID

from app.processing import tier2_hard_variant as hard
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.tier2_accuracy import organization_record, tier_groupings

CONFIG = load_fuzzy_config()
TRUTH = {"organization_duplicates": [{"record_ids": ["org-1", "org-2"]}]}


def record(number, name, phones=("+1",), **fields):
    defaults = {
        "external_id": f"org-{number}",
        "external_ids": {},
        "attributes": {"identifiers": {"phones": list(phones)}},
        "name_normalized": name,
        "city": "quebec",
    }
    return organization_record(UUID(int=number), UUID(int=1), **(defaults | fields))


def test_hard_variant_respells_the_duplicate_and_drops_its_identifiers():
    # Arrange
    original = record(1, "boulangerie tremblay", domain="tremblay.ca")
    duplicate = record(
        2,
        "boulangerie tremblay",
        domain="tremblay.ca",
        linkedin="company/tremblay",
        external_ids={"crm_id": ["CRM-1"]},
        attributes={"identifiers": {"emails": ["info@tremblay.ca"], "phones": ["+2"]}},
    )
    # Act
    first, second, *_ = hard.hard_variant([original, duplicate], TRUTH, 7, CONFIG.generic_tokens)
    again = hard.hard_variant([original, duplicate], TRUTH, 7, CONFIG.generic_tokens)[1]
    # Assert
    assert first.name_normalized == "boulangerie tremblay" and first.domain == "tremblay.ca"
    assert second.name_normalized != "boulangerie tremblay"
    assert second.name_normalized == again.name_normalized
    assert (second.domain, second.linkedin, second.external_ids) == (None, None, {})
    identifiers = second.attributes["identifiers"]
    assert (identifiers["emails"], identifiers["domains"], identifiers["linkedin"]) == ([], [], [])
    assert duplicate.domain == "tremblay.ca"


def test_about_half_the_duplicates_keep_the_original_phone():
    # Arrange
    original = record(1, "boulangerie tremblay", phones=["+1"])
    duplicate = record(2, "boulangerie tremblay", phones=["+2"])
    # Act
    phones = [
        hard.hard_variant([original, duplicate], TRUTH, seed, CONFIG.generic_tokens)[1].attributes[
            "identifiers"
        ]["phones"]
        for seed in range(40)
    ]
    # Assert
    kept = phones.count(["+1"])
    assert 10 < kept < 30 and kept + phones.count(["+2"]) == 40


def test_name_variants_change_the_name():
    # Arrange
    names = ["boulangerie tremblay", "garage roy and fils", "lll", "ab", "x"]
    # Act
    variants = {
        (name, seed): hard.name_variant(name, CONFIG.generic_tokens, random.Random(seed))
        for name in names
        for seed in range(30)
    }
    # Assert
    assert all(variant != name for (name, _), variant in variants.items())


def test_negatives_are_near_miss_pairs_in_one_city_outside_the_ground_truth():
    # Arrange
    records = [record(1, "boulangerie tremblay"), record(2, "garage roy", city="levis")]
    # Act
    negatives = hard.near_miss_negatives(records, seed=3)
    # Assert
    pairs = list(zip(negatives[::2], negatives[1::2], strict=True))
    assert pairs and all(left.city == right.city for left, right in pairs)
    assert all(left.name_normalized != right.name_normalized for left, right in pairs)
    assert all(item.external_id.startswith("negative-") for item in negatives)
    assert len({item.id for item in negatives}) == len(negatives)
    kinds = {item.external_id.split("-")[1] for item in negatives}
    assert kinds == {"surname", "gendered", "swapped", "branch"}


def test_negatives_skip_names_the_tenant_already_has():
    # Arrange
    taken = [record(n, f"{trade} martin") for n, trade in enumerate(hard.TRADES, start=1)]
    # Act
    negatives = hard.near_miss_negatives(taken, seed=3)
    # Assert
    assert "martin" not in {item.name_normalized.split()[-1] for item in negatives}


def test_no_negative_pair_is_linked():
    # Arrange
    negatives = hard.near_miss_negatives([record(1, "x")], seed=3)
    # Act
    _, both = tier_groupings(negatives, CONFIG)
    # Assert
    assert both.groups == ()


def test_no_records_no_negatives():
    # Act / Assert
    assert hard.near_miss_negatives([], seed=3) == []

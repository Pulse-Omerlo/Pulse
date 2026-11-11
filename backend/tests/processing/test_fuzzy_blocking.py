# AI contribution: 50% or more AI-generated
"""Which pairs Tier 2 compares."""

from dataclasses import replace

from app.processing import fuzzy_blocking as fb
from tests.processing.fuzzy_helpers import CONFIG, profiles, record


def test_blocking_pairs_only_records_that_share_a_block():
    # Arrange
    records = [
        record(1, "boulangerie tremblay"),
        record(2, "boulangerie tremblai"),
        record(3, "imprimerie gagnon"),
        record(4, "transport roy", city="levis"),
        record(5, "garage cote", city="levis"),
    ]
    # Act
    blocking = fb.candidate_pairs(profiles(records), CONFIG)
    # Assert
    # "trem" is shared; "boulangerie" is generic; "roy" and "cote" share only the city.
    assert blocking.pairs == {(records[0].id, records[1].id), (records[3].id, records[4].id)}
    assert blocking.skipped_blocks == 0


def test_short_numeric_and_generic_tokens_are_not_blocking_keys():
    # Arrange
    [item] = profiles([record(1, "groupe ab 2000 tremblay")])
    # Act
    keys = fb.block_keys(item, CONFIG, frozenset())
    # Assert
    assert keys == {("prefix", "trem")}


def test_tokens_in_a_large_share_of_records_do_not_block():
    # Arrange
    common = replace(CONFIG, max_token_share=0.5, min_common_count=2)
    rare = replace(CONFIG, max_token_share=0.5, min_common_count=1000)
    records = [record(n, f"tremblay x{n}a") for n in range(1, 5)] + [record(9, "gagnon")]
    # Act
    with_share = fb.candidate_pairs(profiles(records), common)
    without_share = fb.candidate_pairs(profiles(records), rare)
    # Assert
    # "tremblay" is in 4 of 5 records; every other token is unique.
    assert with_share.pairs == frozenset()
    assert len(without_share.pairs) == 6


def test_block_over_the_cap_is_not_expanded():
    # Arrange
    config = replace(CONFIG, max_block_size=50, min_common_count=1000)
    records = [record(n, f"tremblay {n}") for n in range(1, 201)]
    # Act
    blocking = fb.candidate_pairs(profiles(records), config)
    # Assert
    assert blocking.skipped_blocks == 1
    assert blocking.pairs == frozenset()

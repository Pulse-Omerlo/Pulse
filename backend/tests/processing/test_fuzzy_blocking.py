# AI contribution: 50% or more AI-generated
"""Which pairs Tier 2 compares."""

from dataclasses import replace
from math import comb

from app.processing import fuzzy_blocking as fb
from tests.processing.fuzzy_helpers import CONFIG, profiles, record

# Different first letters, so these filler names never share a word prefix.
FILLER_WORDS = [f"{a}{b}zzz" for a in "abcdefghijklmnopqrstuvwxy" for b in "abcdefghij"]


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
    assert blocking.stats.split_blocks == 0


def test_three_letter_words_still_block():
    # Arrange
    records = [record(1, "transport roy"), record(2, "garage roy"), record(3, "garage ro")]
    # Act
    blocking = fb.candidate_pairs(profiles(records), CONFIG)
    # Assert
    # "ro" is shorter than min_token_length and forms no block.
    assert blocking.pairs == {(records[0].id, records[1].id)}


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


def test_a_token_exactly_at_the_limit_still_blocks():
    # Arrange
    config = replace(CONFIG, max_token_share=0.0, min_common_count=2)
    items = profiles([record(1, "tremblay a"), record(2, "tremblay b"), record(3, "gagnon")])
    # Act
    common = fb.common_words(items, config)
    # Assert
    assert "tremblay" not in common


def test_block_of_exactly_the_cap_is_compared_in_full():
    # Arrange
    config = replace(CONFIG, max_block_size=5, min_common_count=1000)
    records = [record(n, f"tremblay {n}") for n in range(1, 6)]
    # Act
    blocking = fb.candidate_pairs(profiles(records, config), config)
    # Assert
    assert len(blocking.pairs) == comb(5, 2) and blocking.stats.split_blocks == 0


def test_pair_in_an_oversized_city_and_prefix_block_is_still_found():
    # Arrange
    config = replace(CONFIG, max_block_size=50, min_common_count=1000)
    pair = [
        record(1, "boulangerie tremblay", city="quebec"),
        record(2, "boulangerie tremblai", city="quebec"),
    ]
    # 60 other records in Quebec, and 60 other "trem" words elsewhere: both blocks are too big.
    same_city = [record(10 + n, f"garage {FILLER_WORDS[n]}", city="quebec") for n in range(60)]
    same_prefix = [record(100 + n, f"tremblay x{n}y", city="levis") for n in range(60)]
    # Act
    blocking = fb.candidate_pairs(profiles([*pair, *same_city, *same_prefix], config), config)
    # Assert
    assert (pair[0].id, pair[1].id) in blocking.pairs
    # Quebec, Levis and "trem" are split; the Levis "trem" records stay one big block.
    assert blocking.stats.split_blocks == 3
    assert blocking.stats.windowed_blocks == 1


def test_split_block_still_over_the_cap_is_windowed_and_bounded():
    # Arrange
    config = replace(CONFIG, max_block_size=50, window_size=10, min_common_count=1000)
    records = [record(n, f"tremblay {n:03d}", city="quebec") for n in range(1, 201)]
    # Act
    blocking = fb.candidate_pairs(profiles(records, config), config)
    # Assert
    # 200 records in one block would be 19,900 comparisons.
    assert len(blocking.pairs) <= 200 * config.window_size
    assert blocking.stats.windowed_blocks == 1
    assert blocking.stats.pairs_left_out == comb(200, 2) - len(blocking.pairs)


def test_oversized_block_members_without_the_other_key_are_kept_together():
    # Arrange
    config = replace(CONFIG, max_block_size=3, min_common_count=1000)
    records = [record(n, f"tremblay {n}") for n in range(1, 5)]
    # Act
    blocking = fb.candidate_pairs(profiles(records, config), config)
    # Assert
    # No city to split on: the four records stay one block, compared in a window.
    assert blocking.stats.windowed_blocks == 1
    assert len(blocking.pairs) == comb(4, 2)


def test_stats_add_up():
    # Act
    total = fb.BlockingStats(1, 2, 3, 4) + fb.BlockingStats(10, 20, 30, 40)
    # Assert
    assert total == fb.BlockingStats(11, 22, 33, 44)

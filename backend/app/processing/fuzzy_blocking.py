# AI contribution: 50% or more AI-generated
"""Which pairs of one tenant's records Tier 2 compares."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import comb
from uuid import UUID

from app.processing.fuzzy_config import FuzzyConfig
from app.processing.fuzzy_profile import Profile
from app.processing.match_rules import Pair

Key = tuple[str, ...]


@dataclass(frozen=True)
class BlockingStats:
    """How much of the work was bounded, so a big tenant shows what it skipped."""

    blocks: int = 0
    # Blocks over max_block_size, split by city and word prefix.
    split_blocks: int = 0
    # Split blocks still over max_block_size, compared in a sorted window instead.
    windowed_blocks: int = 0
    # Pairs inside windowed blocks that the window did not compare (another block may have).
    pairs_left_out: int = 0

    def __add__(self, other: "BlockingStats") -> "BlockingStats":
        return BlockingStats(
            self.blocks + other.blocks,
            self.split_blocks + other.split_blocks,
            self.windowed_blocks + other.windowed_blocks,
            self.pairs_left_out + other.pairs_left_out,
        )


@dataclass(frozen=True)
class Blocking:
    pairs: frozenset[Pair]
    stats: BlockingStats


def common_words(profiles: Sequence[Profile], config: FuzzyConfig) -> frozenset[str]:
    counts = Counter(word for item in profiles for word in item.block_words)
    limit = max(config.max_token_share * len(profiles), config.min_common_count)
    return frozenset(word for word, count in counts.items() if count > limit)


def _prefixes(item: Profile, config: FuzzyConfig, common: frozenset[str]) -> set[str]:
    return {word[: config.prefix_length] for word in item.block_words - common}


def block_keys(item: Profile, config: FuzzyConfig, common: frozenset[str]) -> set[Key]:
    keys: set[Key] = {("prefix", prefix) for prefix in _prefixes(item, config, common)}
    keys |= {("city", city) for city in item.signals.cities}
    return keys


def _split_keys(key: Key, item: Profile, config: FuzzyConfig, common: frozenset[str]) -> set[Key]:
    """Narrower keys for a record of an oversized block: its city and word prefix together."""
    kind, value = key
    if kind == "city":
        prefixes = _prefixes(item, config, common)
        return {("city+prefix", value, prefix) for prefix in prefixes} or {("city", value, "")}
    cities = item.signals.cities
    return {("city+prefix", city, value) for city in cities} or {("prefix", value, "")}


def _window(ordered: Sequence[UUID], size: int) -> set[Pair]:
    """Each record with the next `size` records in name order (sorted neighbourhood)."""
    return {
        (min(first, second), max(first, second))
        for index, first in enumerate(ordered)
        for second in ordered[index + 1 : index + 1 + size]
    }


def _group(profiles: Iterable[Profile], keys) -> dict[Key, set[UUID]]:
    blocks: dict[Key, set[UUID]] = defaultdict(set)
    for item in profiles:
        for key in keys(item):
            blocks[key].add(item.id)
    return blocks


def _expand(
    blocks: Mapping[Key, set[UUID]], by_id: Mapping[UUID, Profile], config: FuzzyConfig
) -> tuple[set[Pair], int, int]:
    """Every pair of the blocks that fit, a sorted window over the others."""
    pairs: set[Pair] = set()
    windowed = left_out = 0
    for members in blocks.values():
        if len(members) <= config.max_block_size:
            pairs.update(combinations(sorted(members), 2))
            continue
        ordered = sorted(members, key=lambda record_id: (by_id[record_id].name, record_id))
        found = _window(ordered, config.window_size)
        pairs |= found
        windowed += 1
        left_out += comb(len(ordered), 2) - len(found)
    return pairs, windowed, left_out


def candidate_pairs(profiles: Sequence[Profile], config: FuzzyConfig) -> Blocking:
    """Pairs of one tenant's records that share a block.

    A block over max_block_size is split by city and word prefix; a split block still over
    it is compared in a window of window_size records, so the work stays below
    blocks * max(max_block_size**2 / 2, n * window_size) instead of n**2 / 2.
    """
    common = common_words(profiles, config)
    by_id = {item.id: item for item in profiles}
    primary = _group(profiles, lambda item: block_keys(item, config, common))
    oversized = {
        key: members for key, members in primary.items() if len(members) > config.max_block_size
    }
    fitting = {key: members for key, members in primary.items() if key not in oversized}
    pairs, _, _ = _expand(fitting, by_id, config)
    split: dict[Key, set[UUID]] = defaultdict(set)
    for key, members in oversized.items():
        for record_id in members:
            for narrow in _split_keys(key, by_id[record_id], config, common):
                split[narrow].add(record_id)
    split_pairs, windowed, left_out = _expand(split, by_id, config)
    stats = BlockingStats(len(primary), len(oversized), windowed, left_out)
    return Blocking(frozenset(pairs | split_pairs), stats)

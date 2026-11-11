# AI contribution: 50% or more AI-generated
"""Which pairs of one tenant's records Tier 2 compares."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from app.processing.fuzzy_config import FuzzyConfig
from app.processing.fuzzy_profile import Profile
from app.processing.match_rules import Pair

Key = tuple[str, ...]


@dataclass(frozen=True)
class Blocking:
    pairs: frozenset[Pair]
    # Blocks over max_block_size, left unexpanded.
    skipped_blocks: int


def common_words(profiles: Sequence[Profile], config: FuzzyConfig) -> frozenset[str]:
    counts = Counter(word for item in profiles for word in item.block_words)
    limit = max(config.max_token_share * len(profiles), config.min_common_count)
    return frozenset(word for word, count in counts.items() if count > limit)


def block_keys(item: Profile, config: FuzzyConfig, common: frozenset[str]) -> set[Key]:
    keys: set[Key] = {
        ("prefix", word[: config.prefix_length]) for word in item.block_words - common
    }
    keys |= {("city", city) for city in item.signals.cities}
    return keys


def candidate_pairs(profiles: Sequence[Profile], config: FuzzyConfig) -> Blocking:
    """Pairs of one tenant's records that share a block.

    At most blocks * max_block_size**2 / 2 pairs, instead of n**2 / 2 for every pair.
    """
    common = common_words(profiles, config)
    blocks: dict[Key, list[UUID]] = defaultdict(list)
    for item in profiles:
        for key in block_keys(item, config, common):
            blocks[key].append(item.id)
    pairs: set[Pair] = set()
    skipped = 0
    for members in blocks.values():
        if len(members) > config.max_block_size:
            skipped += 1
            continue
        pairs.update(combinations(sorted(members), 2))
    return Blocking(frozenset(pairs), skipped)

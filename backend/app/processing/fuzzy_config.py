# AI contribution: 50% or more AI-generated
"""Settings of Tier 2 fuzzy name matching, read from fuzzy_matching.toml."""

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

FUZZY_CONFIG_PATH = Path(__file__).with_name("fuzzy_matching.toml")


class FuzzyConfigError(ValueError):
    """fuzzy_matching.toml is missing a setting or has an invalid one."""


@dataclass(frozen=True)
class FuzzyConfig:
    threshold: float
    min_token_length: int
    prefix_length: int
    max_token_share: float
    min_common_count: int
    max_block_size: int
    generic_tokens: frozenset[str]


def _number(data: dict[str, Any], key: str, low: float, high: float) -> float:
    value = data.get(key)
    # bool is an int in Python, but `threshold = true` is a mistake.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FuzzyConfigError(f"fuzzy_matching.toml: {key} must be a number, got {value!r}")
    if not low <= value <= high:
        raise FuzzyConfigError(f"fuzzy_matching.toml: {key} must be between {low} and {high}")
    return float(value)


def _whole(data: dict[str, Any], key: str, low: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise FuzzyConfigError(
            f"fuzzy_matching.toml: {key} must be a whole number of at least {low}, got {value!r}"
        )
    return value


def _tokens(data: dict[str, Any]) -> frozenset[str]:
    values = data.get("generic_tokens")
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise FuzzyConfigError("fuzzy_matching.toml: generic_tokens must be a list of words")
    return frozenset(value.strip().lower() for value in values)


def parse_fuzzy_config(data: dict[str, Any]) -> FuzzyConfig:
    """Validate the settings, failing on the first bad one."""
    return FuzzyConfig(
        # Below 0.5 almost any two names of the same length would be proposed.
        threshold=_number(data, "threshold", 0.5, 1.0),
        min_token_length=_whole(data, "min_token_length", 1),
        prefix_length=_whole(data, "prefix_length", 1),
        max_token_share=_number(data, "max_token_share", 0.0, 1.0),
        min_common_count=_whole(data, "min_common_count", 1),
        max_block_size=_whole(data, "max_block_size", 2),
        generic_tokens=_tokens(data),
    )


@cache
def load_fuzzy_config() -> FuzzyConfig:
    with FUZZY_CONFIG_PATH.open("rb") as handle:
        return parse_fuzzy_config(tomllib.load(handle))

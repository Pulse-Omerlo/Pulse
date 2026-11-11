# AI contribution: 50% or more AI-generated
"""Settings of Tier 2 fuzzy name matching, read from fuzzy_matching.toml."""

import tomllib
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path
from typing import Any

from app.processing.normalization import CONNECTOR_ALIASES, CONNECTOR_TOKEN, normalize_text

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
    window_size: int
    max_name_words: int
    generic_tokens: frozenset[str]


def _error(message: str) -> FuzzyConfigError:
    return FuzzyConfigError(f"fuzzy_matching.toml: {message}")


def _present(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise _error(f"{key} is missing")
    return data[key]


def _number(data: dict[str, Any], key: str, low: float, high: float) -> float:
    value = _present(data, key)
    # bool is an int in Python, but `threshold = true` is a mistake.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _error(f"{key} must be a number, got {value!r}")
    if not low <= value <= high:
        raise _error(f"{key} must be between {low} and {high}, got {value!r}")
    return float(value)


def _whole(data: dict[str, Any], key: str, low: int, high: int) -> int:
    value = _present(data, key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _error(f"{key} must be a whole number from {low} to {high}, got {value!r}")
    return value


def generic_word(value: object) -> str:
    """A generic word normalized like the names it is compared with (accents, case, "et")."""
    word = normalize_text(value) if isinstance(value, str) else None
    if word is None:
        raise _error(f"generic_tokens entries must be words, got {value!r}")
    if " " in word:
        raise _error(f"generic_tokens entries must be single words, got {value!r}")
    return CONNECTOR_TOKEN if word in CONNECTOR_ALIASES else word


def _tokens(data: dict[str, Any]) -> frozenset[str]:
    values = _present(data, "generic_tokens")
    if not isinstance(values, list):
        raise _error(f"generic_tokens must be a list of words, got {values!r}")
    return frozenset(generic_word(value) for value in values)


def parse_fuzzy_config(data: dict[str, Any]) -> FuzzyConfig:
    """Validate the settings, failing on the first bad one."""
    unknown = sorted(data.keys() - {field.name for field in fields(FuzzyConfig)})
    if unknown:
        raise _error(f"unknown settings: {', '.join(unknown)}")
    return FuzzyConfig(
        # Below 0.5 almost any two names of the same length would be proposed.
        threshold=_number(data, "threshold", 0.5, 1.0),
        min_token_length=_whole(data, "min_token_length", 1, 10),
        # A longer prefix no longer catches a typo in the middle of a short word.
        prefix_length=_whole(data, "prefix_length", 2, 8),
        max_token_share=_number(data, "max_token_share", 0.0, 1.0),
        min_common_count=_whole(data, "min_common_count", 1, 100_000),
        # One block of 500 records is already 124,750 comparisons.
        max_block_size=_whole(data, "max_block_size", 2, 500),
        window_size=_whole(data, "window_size", 1, 100),
        max_name_words=_whole(data, "max_name_words", 1, 50),
        generic_tokens=_tokens(data),
    )


@cache
def load_fuzzy_config() -> FuzzyConfig:
    with FUZZY_CONFIG_PATH.open("rb") as handle:
        return parse_fuzzy_config(tomllib.load(handle))

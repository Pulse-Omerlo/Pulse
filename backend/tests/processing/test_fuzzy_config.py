# AI contribution: 50% or more AI-generated
"""Loading and validating fuzzy_matching.toml."""

import pytest

from app.processing import fuzzy_config
from app.processing.fuzzy_config import FuzzyConfigError, load_fuzzy_config, parse_fuzzy_config

VALID = {
    "threshold": 0.87,
    "min_token_length": 3,
    "prefix_length": 4,
    "max_token_share": 0.1,
    "min_common_count": 10,
    "max_block_size": 50,
    "generic_tokens": [" Groupe ", "de"],
}


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "fuzzy.toml"
    monkeypatch.setattr(fuzzy_config, "FUZZY_CONFIG_PATH", path)
    load_fuzzy_config.cache_clear()
    yield path
    load_fuzzy_config.cache_clear()


def test_valid_settings_are_parsed():
    # Act
    config = parse_fuzzy_config(VALID)
    # Assert
    assert config.threshold == 0.87
    assert config.max_block_size == 50
    assert config.generic_tokens == frozenset({"groupe", "de"})


def test_shipped_file_is_valid_and_its_threshold_is_in_the_planned_range():
    # Arrange
    load_fuzzy_config.cache_clear()
    # Act
    config = load_fuzzy_config()
    # Assert
    assert 0.85 <= config.threshold <= 0.90
    assert {"restaurant", "pharmacies", "depanneur", "residence", "canada"} <= config.generic_tokens
    assert not {"quebec", "montreal", "ete"} & config.generic_tokens


def test_whole_number_threshold_is_accepted():
    # Act
    config = parse_fuzzy_config(VALID | {"threshold": 1})
    # Assert
    assert config.threshold == 1.0


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("threshold", "0.87", "threshold must be a number, got '0.87'"),
        ("threshold", True, "threshold must be a number"),
        ("threshold", None, "threshold must be a number"),
        ("threshold", 87, "threshold must be between 0.5 and 1.0, got 87"),
        ("threshold", 0.2, "threshold must be between 0.5 and 1.0"),
        ("max_token_share", 1.5, "max_token_share must be between"),
        ("min_token_length", 0, "min_token_length must be a whole number from 1 to 10"),
        # True would pass as 1 if booleans were not refused.
        ("min_token_length", True, "min_token_length must be a whole number"),
        ("prefix_length", 2.5, "prefix_length must be a whole number"),
        ("prefix_length", 9, "prefix_length must be a whole number from 2 to 8"),
        ("max_block_size", 1, "max_block_size must be a whole number from 2 to 500"),
        ("max_block_size", 501, "max_block_size must be a whole number from 2 to 500"),
        ("max_block_size", True, "max_block_size must be a whole number"),
        ("min_common_count", False, "min_common_count must be a whole number"),
        ("generic_tokens", "groupe", "generic_tokens must be a list of words"),
        ("generic_tokens", ["groupe", " "], "generic_tokens entries must be words"),
        ("generic_tokens", [3], "generic_tokens entries must be words"),
        ("generic_tokens", ["chez nous"], "generic_tokens entries must be single words"),
    ],
)
def test_bad_setting_fails_with_its_name(key, value, message):
    # Arrange
    data = VALID | {key: value}
    # Act
    with pytest.raises(FuzzyConfigError) as error:
        parse_fuzzy_config(data)
    # Assert
    assert message in str(error.value)


def test_missing_setting_is_named_as_missing():
    # Arrange
    data = {key: value for key, value in VALID.items() if key != "threshold"}
    # Act / Assert
    with pytest.raises(FuzzyConfigError, match="threshold is missing"):
        parse_fuzzy_config(data)


def test_unknown_settings_are_refused():
    # Arrange
    data = VALID | {"treshold": 0.9, "window": 3}
    # Act / Assert
    with pytest.raises(FuzzyConfigError, match="unknown settings: treshold, window"):
        parse_fuzzy_config(data)


def test_generic_words_are_normalized_like_names():
    # Arrange
    data = VALID | {"generic_tokens": ["Épicerie", " Frères ", "et", "D'"]}
    # Act
    config = parse_fuzzy_config(data)
    # Assert
    assert config.generic_tokens == frozenset({"epicerie", "freres", "and", "d"})


def test_file_is_read_once(config_file):
    # Arrange
    config_file.write_text(
        "threshold = 0.9\nmin_token_length = 3\nprefix_length = 4\n"
        "max_token_share = 0.1\nmin_common_count = 10\nmax_block_size = 50\n"
        'generic_tokens = ["de"]\n'
    )
    # Act
    first = load_fuzzy_config()
    config_file.write_text("threshold = 0.5")
    # Assert
    assert first.threshold == 0.9
    assert load_fuzzy_config() is first


def test_bad_file_fails_on_load(config_file):
    # Arrange
    config_file.write_text('threshold = "high"')
    # Act / Assert
    with pytest.raises(FuzzyConfigError, match="threshold must be a number"):
        load_fuzzy_config()

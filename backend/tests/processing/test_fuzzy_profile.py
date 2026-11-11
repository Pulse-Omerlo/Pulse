# AI contribution: 50% or more AI-generated
"""What Tier 2 reads from each record."""

from dataclasses import replace

from app.processing import fuzzy_profile as fp
from tests.processing.fuzzy_helpers import CONFIG, EXCLUDED, record


def test_signals_read_identifiers_and_ignore_bad_values():
    # Arrange
    item = record(
        1,
        "x",
        city="quebec",
        attributes={
            "cities": ["levis", 3],
            "identifiers": {"phones": "not a list", "emails": ["info@tremblay.ca", "bad"]},
        },
    )
    # Act
    found = fp.signals(item, EXCLUDED)
    # Assert
    assert found.cities == {"quebec", "levis"}
    assert found.phones == frozenset()
    assert found.email_domains == {"tremblay.ca"}


def test_signals_of_a_record_without_attributes_are_empty():
    # Arrange
    item = record(1, "x", attributes=None)
    # Act
    found = fp.signals(item, EXCLUDED)
    # Assert
    assert not (found.cities or found.phones or found.email_domains)


def test_profile_splits_the_name_once():
    # Arrange
    item = record(1, "garage 2000 tremblay and fils", city="levis")
    # Act
    found = fp.profile(item, CONFIG, EXCLUDED)
    # Assert
    assert found.numbers == {"2000"}
    assert found.distinctive == ("2000", "tremblay")
    assert found.block_words == {"tremblay"}
    assert found.signals.cities == {"levis"}


def test_a_very_long_name_is_compared_on_its_first_words_only():
    # Arrange
    config = replace(CONFIG, max_name_words=3)
    name = " ".join(f"mot{chr(97 + n % 26)}{chr(97 + n // 26 % 26)}" for n in range(400))
    # Act
    found = fp.name_profile(name, config)
    # Assert
    assert found.words == ("motaa", "motba", "motca")
    assert len(found.distinctive) == 3 and found.name == name

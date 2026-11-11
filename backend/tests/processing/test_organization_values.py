# AI contribution: 50% or more AI-generated
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.processing.clustering import Member
from app.processing.organization_values import (
    choose_display_name,
    choose_display_values,
    choose_primary_domain,
    most_common,
)

DAY = datetime(2026, 11, 1, tzinfo=UTC)


def member(number, name=None, normalized=None, domain=None, days=0, external_id=None):
    return Member(
        UUID(int=number),
        name=name,
        name_normalized=normalized,
        external_id=external_id or f"ext-{number}",
        domain=domain,
        updated=DAY + timedelta(days=days),
    )


def both_orders(items):
    return [list(items), list(reversed(items))]


def test_spellings_of_one_name_vote_together_and_the_most_common_spelling_shows():
    # Arrange
    items = [
        member(1, "LE SOLEIL", "le soleil"),
        member(2, "Le Soleil", "le soleil"),
        member(3, "Le Soleil", "le soleil"),
        member(4, "Journal Exemple", "journal exemple"),
        member(5, "Journal Exemple", "journal exemple", days=9),
    ]
    # Act
    results = [choose_display_name(order) for order in both_orders(items)]
    # Assert
    assert results == ["Le Soleil", "Le Soleil"]


def test_tied_names_go_to_the_most_recently_updated_record_in_either_order():
    # Arrange
    items = [member(1, "Old name", "old name", days=1), member(2, "New name", "new name", days=2)]
    # Act
    results = [choose_display_name(order) for order in both_orders(items)]
    # Assert
    assert results == ["New name", "New name"]


def test_tied_spellings_go_to_the_most_recently_updated_record_in_either_order():
    # Arrange
    items = [member(1, "LE SOLEIL", "le soleil", days=3), member(2, "Le Soleil", "le soleil")]
    # Act
    results = [choose_display_name(order) for order in both_orders(items)]
    # Assert
    assert results == ["LE SOLEIL", "LE SOLEIL"]


def test_a_real_name_beats_normalized_names_and_ids():
    # Arrange
    items = [member(1, normalized="exemple"), member(2, normalized="exemple"), member(3, "Exemple")]
    # Act / Assert
    assert choose_display_name(items) == "Exemple"


def test_without_names_the_normalized_name_then_the_external_id_is_used():
    # Arrange
    normalized = [member(1, normalized="exemple"), member(2)]
    ids_only = [member(1, external_id="ext-9")]
    # Act / Assert
    assert choose_display_name(normalized) == "exemple"
    assert choose_display_name(ids_only) == "ext-9"
    assert choose_display_name([]) is None


def test_missing_values_never_win_even_as_the_majority():
    # Arrange
    items = [member(1), member(2), member(3, "Exemple", "exemple", domain="exemple.ca")]
    # Act / Assert
    assert choose_display_values(items) == ("Exemple", "exemple.ca")


@pytest.mark.parametrize("reverse", [False, True])
def test_tied_domains_go_to_the_most_recently_updated_record(reverse):
    # Arrange
    items = [member(1, domain="old.ca", days=1), member(2, domain="new.ca", days=2)]
    # Act
    result = choose_primary_domain(reversed(items) if reverse else items)
    # Assert
    assert result == "new.ca"


@pytest.mark.parametrize("reverse", [False, True])
def test_same_update_time_falls_back_to_the_larger_record_id(reverse):
    # Arrange
    values = [
        ("first", DAY, UUID(int=2)),
        ("second", DAY, UUID(int=1)),
        ("third", None, UUID(int=3)),
    ]
    # Act
    result = most_common(reversed(values) if reverse else values)
    # Assert
    assert result == "first"

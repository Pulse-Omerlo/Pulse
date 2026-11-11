# AI contribution: 50% or more AI-generated
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.processing.clustering import Member
from app.processing.organization_values import choose_display_values, most_common

DAY = datetime(2026, 11, 1, tzinfo=UTC)


def test_most_common_name_and_domain_win():
    # Arrange
    members = [
        Member(UUID(int=1), name="Exemple Inc.", domain="exemple.ca", updated=DAY),
        Member(UUID(int=2), name="Exemple", domain="exemple.ca", updated=DAY),
        Member(UUID(int=3), name="Exemple", domain="old.ca", updated=DAY + timedelta(days=9)),
    ]
    # Act
    values = choose_display_values(members)
    # Assert
    assert values == ("Exemple", "exemple.ca")


def test_tie_goes_to_the_most_recently_updated_record():
    # Arrange
    members = [
        Member(UUID(int=1), name="Old name", domain="old.ca", updated=DAY + timedelta(days=1)),
        Member(UUID(int=2), name="New name", domain="new.ca", updated=DAY + timedelta(days=2)),
    ]
    # Act
    values = choose_display_values(reversed(members))
    # Assert
    assert values == ("New name", "new.ca")


def test_same_update_time_falls_back_to_the_larger_record_id():
    # Arrange
    values = [
        ("first", DAY, UUID(int=2)),
        ("second", DAY, UUID(int=1)),
        ("third", None, UUID(int=3)),
    ]
    # Act
    result = most_common(values)
    # Assert
    assert result == "first"


def test_generic_domain_is_skipped_and_missing_values_give_none():
    # Arrange: generic domains reach this module as None.
    members = [Member(UUID(int=1), name="Exemple", domain=None)]
    # Act
    values = choose_display_values(members)
    # Assert
    assert values == ("Exemple", None)
    assert choose_display_values([]) == (None, None)

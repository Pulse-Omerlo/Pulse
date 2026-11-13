# AI contribution: 50% or more AI-generated
"""How affiliation rows become the people of the sector mix."""

import uuid

from app.services.affiliations import people_units, strings

ANA, ANA_AGAIN, BOB = (uuid.UUID(int=n) for n in (1, 2, 3))
MERGED_ANA = uuid.UUID(int=10)
ACME, BOREAL = uuid.UUID(int=20), uuid.UUID(int=21)
ACME_RECORD, BOREAL_RECORD = uuid.UUID(int=30), uuid.UUID(int=31)


def row(record, *, person=None, display=None, confidence=None, org_record=None, org=None):
    return (record, "Ana", "Roy", "ana roy", person, display, confidence, org_record, org)


def test_a_person_with_several_records_counts_once_under_the_strongest_affiliation():
    # Arrange
    rows = [
        row(ANA, person=MERGED_ANA, display="Ana Roy", confidence=0.6, org_record=BOREAL_RECORD),
        row(ANA_AGAIN, person=MERGED_ANA, confidence=0.9, org_record=ACME_RECORD, org=ACME),
    ]

    # Act
    (unit,) = people_units(rows)

    # Assert
    assert (unit.id, unit.name, unit.organization_key) == (str(MERGED_ANA), "Ana Roy", str(ACME))


def test_a_person_without_affiliation_is_kept_unplaced():
    # Act
    (unit,) = people_units([row(BOB)])

    # Assert
    assert (unit.id, unit.name, unit.organization_key) == (str(BOB), "Ana Roy", None)


def test_equal_confidence_goes_to_the_lowest_organization_key():
    # Arrange
    rows = [
        row(ANA, confidence=1.0, org_record=BOREAL_RECORD, org=BOREAL),
        row(ANA, confidence=1.0, org_record=ACME_RECORD, org=ACME),
    ]

    # Act
    (unit,) = people_units(rows)

    # Assert
    assert unit.organization_key == str(ACME)


def test_strings_reads_a_text_a_list_or_a_dict_of_texts():
    # Act / Assert
    assert strings("a") == ["a"]
    assert strings(["a", 1, " ", "b"]) == ["a", "b"]
    assert strings({"fr": "a", "en": "b"}) == ["a", "b"]
    assert strings(None) == []

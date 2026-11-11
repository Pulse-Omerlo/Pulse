# AI contribution: 50% or more AI-generated
"""Tier 1 people matching. Database calls are mocked where needed."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models import SourceRecord, Tenant
from app.processing import person_resolution as pr

TENANT = UUID(int=200)
SOURCE = UUID(int=300)


def person(number=1, **fields):
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": TENANT,
                "source_id": SOURCE,
                "kind": "person",
                "external_id": str(number),
                "external_ids": {},
                "attributes": {},
            }
            | fields
        )
    )


def with_identifiers(number, name, *, emails=None, phones=None, linkedin=None, **fields):
    identifiers = {}
    if emails is not None:
        identifiers["emails"] = list(emails)
    if phones is not None:
        identifiers["phones"] = list(phones)
    if linkedin is not None:
        identifiers["linkedin"] = list(linkedin)
    return person(
        number,
        name_normalized=name,
        linkedin=(linkedin or [None])[0],
        attributes={"identifiers": identifiers},
        **fields,
    )


@pytest.mark.parametrize(
    ("left_extra", "right_extra", "rule"),
    [
        (
            {"emails": ["a@example.com"]},
            {"emails": ["a@example.com"]},
            "email",
        ),
        (
            {"phones": ["+14185550100"]},
            {"phones": ["+14185550100"]},
            "phone",
        ),
        (
            {"linkedin": ["in/marie"]},
            {"linkedin": ["in/marie"]},
            "linkedin",
        ),
    ],
)
def test_same_name_plus_secondary_links(left_extra, right_extra, rule):
    # Arrange
    left = with_identifiers(1, "marie tremblay", **left_extra)
    right = with_identifiers(2, "marie tremblay", **right_extra)

    # Act
    match = pr.classify_person_pair(left, right)

    # Assert
    assert match is not None
    assert match.rule == rule
    assert match.strong
    assert "name_normalized" in dict(match.fields)


def test_same_name_and_file_organization_id_links():
    # Arrange
    left = person(
        1,
        name_normalized="marie tremblay",
        attributes={"organization_id": "org-9", "identifiers": {}},
    )
    right = person(
        2,
        name_normalized="marie tremblay",
        attributes={"organization_id": "org-9", "identifiers": {}},
    )

    # Act
    match = pr.classify_person_pair(left, right)

    # Assert
    assert match.rule == "organization_id"
    assert match.strong


def test_organization_id_requires_same_source():
    # Arrange
    left = person(
        1,
        name_normalized="marie tremblay",
        attributes={"organization_id": "org-9"},
    )
    right = person(
        2,
        source_id=UUID(int=999),
        name_normalized="marie tremblay",
        attributes={"organization_id": "org-9"},
    )

    # Act / Assert
    assert pr.classify_person_pair(left, right).rule == "name_only"


def test_name_only_is_a_weak_candidate():
    # Arrange
    left = with_identifiers(1, "jean dupont")
    right = with_identifiers(2, "jean dupont")

    # Act
    match = pr.classify_person_pair(left, right)

    # Assert
    assert match.rule == "name_only"
    assert not match.strong


@pytest.mark.parametrize(
    ("shared", "rule"),
    [
        ({"emails": ["shared@example.com"]}, "email_name_conflict"),
        ({"linkedin": ["in/shared"]}, "linkedin_name_conflict"),
    ],
)
def test_shared_identifier_different_names_are_weak_candidates(shared, rule):
    # Arrange
    left = with_identifiers(1, "alice martin", **shared)
    right = with_identifiers(2, "bob martin", **shared)

    # Act
    match = pr.classify_person_pair(left, right)

    # Assert
    assert match.rule == rule
    assert not match.strong


def test_shared_phone_different_names_do_not_match():
    # Arrange
    left = with_identifiers(1, "alice", phones=["+14185550100"])
    right = with_identifiers(2, "bob", phones=["+14185550100"])

    # Act / Assert
    assert pr.classify_person_pair(left, right) is None


def test_email_beats_phone_when_both_agree():
    # Arrange
    left = with_identifiers(1, "marie", emails=["a@example.com"], phones=["+14185550100"])
    right = with_identifiers(2, "marie", emails=["a@example.com"], phones=["+14185550100"])

    # Act / Assert
    assert pr.classify_person_pair(left, right).rule == "email"


def test_accents_fold_into_the_same_name_key():
    # Arrange: names already normalized the same way the mapper would
    left = with_identifiers(1, "marie eve tremblay", emails=["a@example.com"])
    right = with_identifiers(2, "marie eve tremblay", emails=["a@example.com"])

    # Act / Assert
    assert pr.classify_person_pair(left, right).rule == "email"


@pytest.mark.parametrize(
    "fields",
    [
        {"id": UUID(int=1)},
        {"tenant_id": UUID(int=999)},
        {"kind": "organization"},
        {"valid_to": datetime(2026, 11, 1, tzinfo=UTC)},
    ],
)
def test_invalid_pairs_cannot_match(fields):
    # Arrange
    left = with_identifiers(1, "marie", emails=["a@example.com"])
    right = with_identifiers(2, "marie", emails=["a@example.com"], **fields)

    # Act / Assert
    assert pr.classify_person_pair(left, right) is None
    assert pr.classify_person_pair(right, left) is None


def test_company_linkedin_does_not_block_or_link_people():
    # Arrange
    left = person(1, name_normalized="marie", linkedin="company/acme", attributes={})
    right = person(2, name_normalized="marie", linkedin="company/acme", attributes={})

    # Act
    match = pr.classify_person_pair(left, right)

    # Assert
    assert match.rule == "name_only"


def test_blocking_groups_by_email_and_keeps_tenants_apart():
    # Arrange
    shared = {"emails": ["a@example.com"]}
    items = [
        with_identifiers(1, "marie", **shared),
        with_identifiers(2, "marie", **shared),
        with_identifiers(3, "marie", **shared, tenant_id=UUID(int=999)),
        with_identifiers(4, "marie", **shared, kind="organization"),
        with_identifiers(5, "marie", **shared, valid_to=datetime.now(UTC)),
    ]

    # Act
    matches = pr.blocked_person_matches(items)

    # Assert
    assert len(matches) == 1
    assert matches[0].providePair == (items[0].id, items[1].id)


def test_oversized_blocks_are_skipped(monkeypatch):
    # Arrange
    monkeypatch.setattr(pr, "MAX_PERSON_BLOCK", 2)
    items = [with_identifiers(n, "common name", emails=["same@example.com"]) for n in range(1, 5)]

    # Act
    matches = pr.blocked_person_matches(items)

    # Assert
    assert matches == []


@pytest.mark.parametrize(
    ("first", "second", "conflict"),
    [
        ({"linkedin": ["in/a"]}, {"linkedin": ["in/b"]}, True),
        ({"linkedin": ["in/a"]}, {"linkedin": ["in/a"]}, False),
        ({"emails": ["a@x.com"]}, {"emails": ["b@x.com"]}, False),
    ],
)
def test_conflicting_person_linkedin(first, second, conflict):
    # Arrange
    records = [
        with_identifiers(1, "marie", **first),
        with_identifiers(2, "marie", **second),
    ]

    # Act / Assert
    assert pr.conflicting_person_identifiers(records) is conflict


def test_conflict_candidates_are_not_strong_components():
    # Arrange
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    edges = [
        pr.PersonMatch(a, b, "email", ()),
        pr.PersonMatch(b, c, "email_name_conflict", ()),
    ]

    # Act
    groups = pr.components(edges)

    # Assert
    assert groups == [{a, b}]


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(info={})


def test_empty_tenant_creates_nothing(session):
    # Arrange
    session.scalar.return_value = Tenant(id=TENANT)
    session.scalars.side_effect = [[], [], []]

    # Act
    result = pr.match_people(session, TENANT)

    # Assert
    assert result == pr.PersonMatchResult()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_unknown_tenant_stops_before_loading(session):
    # Arrange
    session.scalar.return_value = None

    # Act
    with pytest.raises(ValueError, match="Unknown tenant"):
        pr.match_people(session, TENANT)

    # Assert
    session.scalars.assert_not_called()

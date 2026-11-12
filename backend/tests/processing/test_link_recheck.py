# AI contribution: 50% or more AI-generated
"""Unit tests for matching-field change detection and evidence checks."""

from uuid import UUID

import pytest

from app.models import SourceRecord
from app.models.people import AUTOMATIC_PERSON_LINK_METHODS
from app.processing.entity_resolution import MatchCandidateData, evidence
from app.processing.link_recheck import (
    MATCHING_FIELDS,
    PERSON_KNOWN_EVIDENCE_FIELDS,
    PERSON_MATCHING_FIELDS,
    PERSON_RECHECK,
    REASON_HOLDS,
    REASON_PARTNER_CHANGED,
    REASON_UNVERIFIABLE,
    REASON_VALUE_CHANGED,
    LinkVerdict,
    evidence_holds,
    matching_changes,
)

TENANT = UUID(int=110)
EXCLUDED = frozenset({"gmail.com", "facebook.com"})


def record(number: int = 1, **fields) -> SourceRecord:
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": TENANT,
                "kind": "organization",
                "external_id": str(number),
                "external_ids": {},
                "attributes": {},
            }
            | fields
        )
    )


def test_matching_fields_cover_tier1_identifiers():
    # Act / Assert
    assert MATCHING_FIELDS == (
        "name_normalized",
        "city",
        "domain",
        "linkedin",
        "external_ids",
    )


def test_identical_versions_have_no_changes():
    # Arrange
    before = record(1, name_normalized="acme", domain="acme.io", city="montreal")
    after = record(2, name_normalized="acme", domain="acme.io", city="montreal")

    # Act / Assert
    assert matching_changes(before, after) == {}


def test_attribute_only_change_is_ignored():
    # Arrange
    before = record(1, name_normalized="acme", attributes={"phone": "1"})
    after = record(2, name_normalized="acme", attributes={"phone": "2", "note": "x"})

    # Act / Assert
    assert matching_changes(before, after) == {}


@pytest.mark.parametrize(
    ("field", "old", "new"),
    [
        ("domain", "acme.io", "new.io"),
        ("domain", "acme.io", None),
        ("domain", None, "acme.io"),
        ("linkedin", "company/acme", "company/other"),
        ("linkedin", "company/acme", None),
        ("city", "montreal", "quebec"),
        ("city", "montreal", None),
        ("name_normalized", "acme", "acme tech"),
    ],
)
def test_each_string_matching_field_change(field, old, new):
    # Arrange
    before = record(1, **{field: old})
    after = record(2, **{field: new})

    # Act / Assert
    assert matching_changes(before, after) == {field: {"before": old, "after": new}}


def test_blank_string_matches_none_for_string_fields():
    # Arrange
    before = record(1, domain="")
    after = record(2, domain=None)

    # Act / Assert
    assert matching_changes(before, after) == {}


def test_external_ids_added_removed_replaced():
    # Arrange
    before = record(1, external_ids={"crm": ["1"]})
    after = record(2, external_ids={"crm": ["2"]})

    # Act / Assert
    assert matching_changes(before, after) == {
        "external_ids": {
            "before": frozenset({("crm", "1")}),
            "after": frozenset({("crm", "2")}),
        }
    }


def test_external_ids_noise_is_not_a_change():
    # Arrange
    before = record(1, external_ids={"crm": ["1", "1"], "x": [" a "]})
    after = record(2, external_ids={"x": ["a"], "crm": ["1"]})

    # Act / Assert
    assert matching_changes(before, after) == {}


def test_external_ids_invalid_entries_are_ignored():
    # Arrange
    before = record(1, external_ids={"": ["1"], "crm": "bad", "ok": ["1"]})
    after = record(2, external_ids={"ok": ["1"]})

    # Act / Assert
    assert matching_changes(before, after) == {}


def test_external_ids_none_equals_empty():
    # Arrange
    before = record(1, external_ids=None)
    after = record(2, external_ids={})

    # Act / Assert
    assert matching_changes(before, after) == {}


def test_domain_evidence_holds_when_unchanged():
    # Arrange
    left = record(1, domain="acme.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_domain_evidence_fails_when_self_value_changes():
    # Arrange
    left = record(1, domain="new.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_domain_becoming_generic_fails():
    # Arrange
    left = record(1, domain="gmail.com")
    right = record(2, domain="gmail.com")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "gmail.com"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_linkedin_removed_fails():
    # Arrange
    left = record(1, linkedin=None)
    right = record(2, linkedin="company/acme")
    match = MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", "company/acme"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("linkedin", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_linkedin_person_url_is_ineligible():
    # Arrange
    left = record(1, linkedin="in/marie")
    right = record(2, linkedin="in/marie")
    match = MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", "in/marie"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("linkedin", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_name_city_requires_both_entries():
    # Arrange
    left = record(1, name_normalized="acme", city="quebec")
    right = record(2, name_normalized="acme", city="montreal")
    match = MatchCandidateData(
        left.id,
        right.id,
        "name_city",
        (("name_normalized", "acme"), ("city", "montreal")),
    )
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("name_city", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_name_city_city_emptied_fails():
    # Arrange
    left = record(1, name_normalized="acme", city=None)
    right = record(2, name_normalized="acme", city="montreal")
    match = MatchCandidateData(
        left.id,
        right.id,
        "name_city",
        (("name_normalized", "acme"), ("city", "montreal")),
    )
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("name_city", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_external_id_still_shared_among_aliases_holds():
    # Arrange
    left = record(1, external_ids={"crm": ["old", "123"]})
    right = record(2, external_ids={"crm": ["123"]})
    match = MatchCandidateData(left.id, right.id, "external_id", (("external_ids.crm", "123"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("external_id", payload, left, [left, right], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_external_ids_namespace_splits_on_first_dot_only():
    # Arrange
    left = record(1, external_ids={"crm.v1": ["123"]})
    right = record(2, external_ids={"crm.v1": ["123"]})
    payload = [
        {
            "rule": "external_id",
            "rule_set_version": "tier1-v1",
            "field": "external_ids.crm.v1",
            "value": "123",
            "matched_value": "123",
            "matched_record_id": str(right.id),
        }
    ]

    # Act
    verdict = evidence_holds("external_id", payload, left, [left, right], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_partner_changed_when_no_member_carries_matched_value():
    # Arrange
    left = record(1, domain="acme.io")
    right = record(2, domain="other.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    # Act
    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_PARTNER_CHANGED


def test_name_city_partner_must_carry_both_fields():
    # Arrange
    left = record(1, name_normalized="acme", city="montreal")
    name_only = record(2, name_normalized="acme", city="quebec")
    city_only = record(3, name_normalized="other", city="montreal")
    match = MatchCandidateData(
        left.id,
        name_only.id,
        "name_city",
        (("name_normalized", "acme"), ("city", "montreal")),
    )
    payload = evidence(match, name_only.id)

    # Act
    verdict = evidence_holds("name_city", payload, left, [left, name_only, city_only], EXCLUDED)

    # Assert
    assert not verdict.holds and verdict.reason == REASON_PARTNER_CHANGED


def test_solo_member_cannot_contradict_partner_side():
    # Arrange
    alone = record(1, domain="acme.io")
    match = MatchCandidateData(alone.id, UUID(int=99), "domain", (("domain", "acme.io"),))
    payload = evidence(match, UUID(int=99))

    # Act
    verdict = evidence_holds("domain", payload, alone, [alone], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_empty_evidence_is_unverifiable_keep():
    # Arrange
    left = record(1, domain="acme.io")

    # Act
    verdict = evidence_holds("domain", [], left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_manual_method_is_unverifiable_keep():
    # Arrange
    left = record(1, domain="acme.io")

    # Act
    verdict = evidence_holds("manual", [{"decided_by": "analyst"}], left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_unknown_field_is_unverifiable():
    # Arrange
    left = record(1, domain="acme.io")
    payload = [
        {
            "rule": "domain",
            "rule_set_version": "tier1-v1",
            "field": "unknown",
            "value": "x",
            "matched_value": "x",
            "matched_record_id": str(UUID(int=2)),
        }
    ]

    # Act
    verdict = evidence_holds("domain", payload, left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_empty_external_ids_namespace_is_unverifiable():
    # Arrange
    left = record(1, domain="acme.io")
    payload = [
        {
            "rule": "external_id",
            "rule_set_version": "tier1-v1",
            "field": "external_ids.",
            "value": "123",
            "matched_value": "123",
            "matched_record_id": str(UUID(int=2)),
        }
    ]

    # Act
    verdict = evidence_holds("external_id", payload, left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_rule_mismatch_with_link_method_is_unverifiable():
    # Arrange
    left = record(1, domain="acme.io")
    payload = [
        {
            "rule": "linkedin",
            "rule_set_version": "tier1-v1",
            "field": "domain",
            "value": "acme.io",
            "matched_value": "acme.io",
            "matched_record_id": str(UUID(int=2)),
        }
    ]

    # Act
    verdict = evidence_holds("domain", payload, left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


@pytest.mark.parametrize(
    ("link_method", "payload"),
    [
        (
            "domain",
            [
                {
                    "rule": 1,
                    "field": "domain",
                    "value": "a",
                    "matched_value": "a",
                    "matched_record_id": "x",
                }
            ],
        ),
        (
            "domain",
            [
                {
                    "rule": "domain",
                    "field": ["domain"],
                    "value": "a",
                    "matched_value": "a",
                    "matched_record_id": "x",
                }
            ],
        ),
        (
            "domain",
            [
                {
                    "rule": "domain",
                    "field": "domain",
                    "value": ["a"],
                    "matched_value": "a",
                    "matched_record_id": "x",
                }
            ],
        ),
        (
            "external_id",
            [
                {
                    "rule": "external_id",
                    "field": "external_ids.crm",
                    "value": "1",
                    "matched_value": ["1"],
                    "matched_record_id": "x",
                }
            ],
        ),
    ],
)
def test_malformed_evidence_types_are_unverifiable(link_method, payload):
    # Arrange
    left = record(1, domain="acme.io", external_ids={"crm": ["1"]})

    # Act
    verdict = evidence_holds(link_method, payload, left, [left], EXCLUDED)

    # Assert
    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_partner_side_order_independent():
    # Arrange
    left = record(1, domain="acme.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)
    expected = LinkVerdict(holds=True, reason=REASON_HOLDS)

    # Act
    forward = evidence_holds("domain", payload, left, [left, right], EXCLUDED)
    reverse = evidence_holds("domain", payload, left, [right, left], EXCLUDED)

    # Assert
    assert forward == reverse == expected


def person(number: int = 1, **fields) -> SourceRecord:
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": TENANT,
                "kind": "person",
                "external_id": str(number),
                "external_ids": {},
                "attributes": {"identifiers": {}},
            }
            | fields
        )
    )


def test_person_matching_fields_cover_tier1_identifiers():
    # Act / Assert
    assert PERSON_MATCHING_FIELDS == (
        "name_normalized",
        "linkedin",
        "identifiers.emails",
        "identifiers.phones",
        "organization_id",
    )


def test_person_linkedin_change_is_detected():
    # Arrange
    before = person(1, linkedin="in/bob", attributes={"identifiers": {"linkedin": ["in/bob"]}})
    after = person(2, linkedin="in/alice", attributes={"identifiers": {"linkedin": ["in/alice"]}})

    # Act
    changes = matching_changes(
        before, after, fields=PERSON_MATCHING_FIELDS, value_of=PERSON_RECHECK.value_of
    )

    # Assert
    assert changes["linkedin"] == {
        "before": frozenset({"in/bob"}),
        "after": frozenset({"in/alice"}),
    }


def test_person_email_evidence_holds_across_rename_of_unrelated_attrs():
    # Arrange
    left = person(
        1,
        name_normalized="marie",
        attributes={"identifiers": {"emails": ["a@example.com"]}, "job_titles": {"en": "CEO"}},
    )
    right = person(
        2,
        name_normalized="marie",
        attributes={"identifiers": {"emails": ["a@example.com"]}},
    )
    payload = [
        {
            "rule": "email",
            "rule_set_version": "tier1-v1",
            "field": "name_normalized",
            "value": "marie",
            "matched_value": "marie",
            "matched_record_id": str(right.id),
        },
        {
            "rule": "email",
            "rule_set_version": "tier1-v1",
            "field": "identifiers.emails",
            "value": "a@example.com",
            "matched_value": "a@example.com",
            "matched_record_id": str(right.id),
        },
    ]

    # Act
    verdict = evidence_holds(
        "email",
        payload,
        left,
        [left, right],
        EXCLUDED,
        automatic_methods=AUTOMATIC_PERSON_LINK_METHODS,
        known_fields=PERSON_KNOWN_EVIDENCE_FIELDS,
        record_has=PERSON_RECHECK.record_has,
    )

    # Assert
    assert verdict == LinkVerdict(holds=True, reason=REASON_HOLDS)


def test_person_company_linkedin_does_not_count_as_person_handle():
    # Arrange
    before = person(1, linkedin="company/acme", attributes={})
    after = person(2, linkedin="company/other", attributes={})

    # Act
    changes = matching_changes(
        before, after, fields=PERSON_MATCHING_FIELDS, value_of=PERSON_RECHECK.value_of
    )

    # Assert
    assert "linkedin" not in changes

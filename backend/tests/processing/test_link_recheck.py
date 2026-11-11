# AI contribution: 50% or more AI-generated
"""Unit tests for matching-field change detection and evidence checks."""

from uuid import UUID

import pytest

from app.models import SourceRecord
from app.processing.entity_resolution import MatchCandidateData, evidence
from app.processing.link_recheck import (
    MATCHING_FIELDS,
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
    assert MATCHING_FIELDS == (
        "name_normalized",
        "city",
        "domain",
        "linkedin",
        "external_ids",
    )


def test_identical_versions_have_no_changes():
    before = record(1, name_normalized="acme", domain="acme.io", city="montreal")
    after = record(2, name_normalized="acme", domain="acme.io", city="montreal")

    assert matching_changes(before, after) == {}


def test_attribute_only_change_is_ignored():
    before = record(1, name_normalized="acme", attributes={"phone": "1"})
    after = record(2, name_normalized="acme", attributes={"phone": "2", "note": "x"})

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
    before = record(1, **{field: old})
    after = record(2, **{field: new})

    assert matching_changes(before, after) == {field: {"before": old, "after": new}}


def test_blank_string_matches_none_for_string_fields():
    before = record(1, domain="")
    after = record(2, domain=None)

    assert matching_changes(before, after) == {}


def test_external_ids_added_removed_replaced():
    before = record(1, external_ids={"crm": ["1"]})
    after = record(2, external_ids={"crm": ["2"]})

    assert matching_changes(before, after) == {
        "external_ids": {
            "before": frozenset({("crm", "1")}),
            "after": frozenset({("crm", "2")}),
        }
    }


def test_external_ids_noise_is_not_a_change():
    before = record(1, external_ids={"crm": ["1", "1"], "x": [" a "]})
    after = record(2, external_ids={"x": ["a"], "crm": ["1"]})

    assert matching_changes(before, after) == {}


def test_external_ids_invalid_entries_are_ignored():
    before = record(1, external_ids={"": ["1"], "crm": "bad", "ok": ["1"]})
    after = record(2, external_ids={"ok": ["1"]})

    assert matching_changes(before, after) == {}


def test_external_ids_none_equals_empty():
    before = record(1, external_ids=None)
    after = record(2, external_ids={})

    assert matching_changes(before, after) == {}


def test_domain_evidence_holds_when_unchanged():
    left = record(1, domain="acme.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_domain_evidence_fails_when_self_value_changes():
    left = record(1, domain="new.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_domain_becoming_generic_fails():
    left = record(1, domain="gmail.com")
    right = record(2, domain="gmail.com")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "gmail.com"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_linkedin_removed_fails():
    left = record(1, linkedin=None)
    right = record(2, linkedin="company/acme")
    match = MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", "company/acme"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("linkedin", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_linkedin_person_url_is_ineligible():
    left = record(1, linkedin="in/marie")
    right = record(2, linkedin="in/marie")
    match = MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", "in/marie"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("linkedin", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_name_city_requires_both_entries():
    left = record(1, name_normalized="acme", city="quebec")
    right = record(2, name_normalized="acme", city="montreal")
    match = MatchCandidateData(
        left.id,
        right.id,
        "name_city",
        (("name_normalized", "acme"), ("city", "montreal")),
    )
    payload = evidence(match, right.id)

    verdict = evidence_holds("name_city", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_VALUE_CHANGED


def test_external_id_still_shared_among_aliases_holds():
    left = record(1, external_ids={"crm": ["old", "123"]})
    right = record(2, external_ids={"crm": ["123"]})
    match = MatchCandidateData(left.id, right.id, "external_id", (("external_ids.crm", "123"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("external_id", payload, left, [left, right], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_partner_changed_when_no_member_carries_matched_value():
    left = record(1, domain="acme.io")
    right = record(2, domain="other.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)

    verdict = evidence_holds("domain", payload, left, [left, right], EXCLUDED)

    assert not verdict.holds and verdict.reason == REASON_PARTNER_CHANGED


def test_solo_member_cannot_contradict_partner_side():
    alone = record(1, domain="acme.io")
    match = MatchCandidateData(alone.id, UUID(int=99), "domain", (("domain", "acme.io"),))
    payload = evidence(match, UUID(int=99))

    verdict = evidence_holds("domain", payload, alone, [alone], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_HOLDS


def test_empty_evidence_is_unverifiable_keep():
    left = record(1, domain="acme.io")

    verdict = evidence_holds("domain", [], left, [left], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_manual_method_is_unverifiable_keep():
    left = record(1, domain="acme.io")

    verdict = evidence_holds("manual", [{"decided_by": "analyst"}], left, [left], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_unknown_field_is_unverifiable():
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

    verdict = evidence_holds("domain", payload, left, [left], EXCLUDED)

    assert verdict.holds and verdict.reason == REASON_UNVERIFIABLE


def test_partner_side_order_independent():
    left = record(1, domain="acme.io")
    right = record(2, domain="acme.io")
    match = MatchCandidateData(left.id, right.id, "domain", (("domain", "acme.io"),))
    payload = evidence(match, right.id)
    expected = LinkVerdict(holds=True, reason=REASON_HOLDS)

    forward = evidence_holds("domain", payload, left, [left, right], EXCLUDED)
    reverse = evidence_holds("domain", payload, left, [right, left], EXCLUDED)

    assert forward == reverse == expected

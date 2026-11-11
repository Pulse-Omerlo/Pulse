# AI contribution: 50% or more AI-generated
"""Unit tests for matching-field change detection."""

from uuid import UUID

import pytest

from app.models import SourceRecord
from app.processing.link_recheck import MATCHING_FIELDS, matching_changes

TENANT = UUID(int=110)


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

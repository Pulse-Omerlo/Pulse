# AI contribution: 50% or more AI-generated
"""Check whether an automatic org link still matches a new source_record version."""

from __future__ import annotations

from typing import Any

from app.models import SourceRecord
from app.processing.entity_resolution import external_keys

MATCHING_FIELDS = (
    "name_normalized",
    "city",
    "domain",
    "linkedin",
    "external_ids",
)


def _absent_string(value: str | None) -> str | None:
    """Treat blank strings as missing, matching cleaning output."""
    if value is None or value == "":
        return None
    return value


def _field_value(record: SourceRecord, name: str) -> Any:
    if name == "external_ids":
        return frozenset(external_keys(record))
    return _absent_string(getattr(record, name))


def matching_changes(before: SourceRecord, after: SourceRecord) -> dict[str, dict[str, Any]]:
    """Return matching fields that differ between two versions.

    Shape matches record_diff.field_changes: {field: {"before": ..., "after": ...}}.
    external_ids are compared as normalized (namespace, value) sets.
    """
    changes: dict[str, dict[str, Any]] = {}
    for name in MATCHING_FIELDS:
        left = _field_value(before, name)
        right = _field_value(after, name)
        if left != right:
            changes[name] = {"before": left, "after": right}
    return changes

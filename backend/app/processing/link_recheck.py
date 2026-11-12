# AI contribution: 50% or more AI-generated
"""Check whether an automatic org link still matches a new source_record version."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.models import SourceRecord
from app.models.resolution import AUTOMATIC_LINK_METHODS, RuleEvidence
from app.processing.entity_resolution import LINKEDIN_COMPANY_PREFIX, external_keys

MATCHING_FIELDS = (
    "name_normalized",
    "city",
    "domain",
    "linkedin",
    "external_ids",
)

REASON_HOLDS = "holds"
REASON_VALUE_CHANGED = "value_changed"
REASON_PARTNER_CHANGED = "partner_changed"
REASON_UNVERIFIABLE = "unverifiable"

EXTERNAL_IDS_PREFIX = "external_ids."
KNOWN_EVIDENCE_FIELDS = frozenset({"domain", "linkedin", "name_normalized", "city"})
RULE_EVIDENCE_KEYS = ("rule", "field", "value", "matched_value", "matched_record_id")
MANUAL_EVIDENCE_KEY = "decided_by"


@dataclass(frozen=True)
class LinkVerdict:
    """Whether an automatic link still holds, and why.

    `unverifiable` reasons keep the link (holds=True). Dropped links use
    value_changed or partner_changed. `kept` counters include unverifiable rows.
    """

    holds: bool
    reason: str


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


def _known_field(field: str) -> bool:
    if field in KNOWN_EVIDENCE_FIELDS:
        return True
    return field.startswith(EXTERNAL_IDS_PREFIX) and len(field) > len(EXTERNAL_IDS_PREFIX)


def _record_has(
    field: str,
    value: str,
    record: SourceRecord,
    excluded_domains: frozenset[str],
) -> bool:
    """True when `record` still carries an eligible matching value for `field`."""
    if field == "domain":
        return bool(record.domain) and record.domain == value and value not in excluded_domains
    if field == "linkedin":
        return (
            bool(record.linkedin)
            and record.linkedin == value
            and value.startswith(LINKEDIN_COMPANY_PREFIX)
        )
    if field == "name_normalized":
        return bool(record.name_normalized) and record.name_normalized == value
    if field == "city":
        return bool(record.city) and record.city == value
    if field.startswith(EXTERNAL_IDS_PREFIX):
        namespace = field[len(EXTERNAL_IDS_PREFIX) :]
        return bool(namespace) and (namespace, value) in external_keys(record)
    return False


def _as_rule_entries(evidence: Sequence[Mapping[str, Any]]) -> list[RuleEvidence] | None:
    """Return rule evidence entries, or None when the payload is empty or malformed."""
    if not evidence:
        return None
    entries: list[RuleEvidence] = []
    for item in evidence:
        if not isinstance(item, Mapping) or MANUAL_EVIDENCE_KEY in item:
            return None
        # Missing keys become None via .get, so one check covers both shape and types.
        if any(not isinstance(item.get(key), str) for key in RULE_EVIDENCE_KEYS):
            return None
        entries.append(cast(RuleEvidence, item))
    return entries


def _partner_supports(
    entries: Sequence[RuleEvidence],
    members: Sequence[SourceRecord],
    excluded_domains: frozenset[str],
    self_id: UUID,
) -> bool:
    """True when some other member carries every evidence matched_value, or none exist.

    Multi-entry rules (name_city) need one partner that satisfies all entries together.
    """
    others = [member for member in members if member.id != self_id]
    if not others:
        return True
    return any(
        all(
            _record_has(entry["field"], entry["matched_value"], member, excluded_domains)
            for entry in entries
        )
        for member in others
    )


def evidence_holds(
    link_method: str,
    evidence: Sequence[Mapping[str, Any]],
    record: SourceRecord,
    members: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
) -> LinkVerdict:
    """Decide whether automatic-link evidence still holds for `record`.

    Self side: every entry's value is still on this record and eligible.
    Partner side: one other current member carries every matched_value, or there is no
    other member to contradict. Empty or malformed evidence is kept as unverifiable.
    """
    if link_method not in AUTOMATIC_LINK_METHODS:
        return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)

    entries = _as_rule_entries(evidence)
    if entries is None:
        return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)

    for entry in entries:
        field = entry["field"]
        rule = entry["rule"]
        if rule != link_method or not _known_field(field):
            return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)
        if not _record_has(field, entry["value"], record, excluded_domains):
            return LinkVerdict(holds=False, reason=REASON_VALUE_CHANGED)

    if not _partner_supports(entries, tuple(members), excluded_domains, record.id):
        return LinkVerdict(holds=False, reason=REASON_PARTNER_CHANGED)

    return LinkVerdict(holds=True, reason=REASON_HOLDS)

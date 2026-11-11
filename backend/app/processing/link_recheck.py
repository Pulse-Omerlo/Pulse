# AI contribution: 50% or more AI-generated
"""Check whether an automatic link still matches a new source_record version."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.models import SourceRecord
from app.models.people import AUTOMATIC_PERSON_LINK_METHODS
from app.models.resolution import AUTOMATIC_LINK_METHODS, RuleEvidence
from app.processing.entity_resolution import LINKEDIN_COMPANY_PREFIX, external_keys
from app.processing.person_resolution import (
    file_organization_id,
    identifier_values,
    person_linkedins,
)

MATCHING_FIELDS = (
    "name_normalized",
    "city",
    "domain",
    "linkedin",
    "external_ids",
)
PERSON_MATCHING_FIELDS = (
    "name_normalized",
    "linkedin",
    "identifiers.emails",
    "identifiers.phones",
    "organization_id",
)

REASON_HOLDS = "holds"
REASON_VALUE_CHANGED = "value_changed"
REASON_PARTNER_CHANGED = "partner_changed"
REASON_UNVERIFIABLE = "unverifiable"

EXTERNAL_IDS_PREFIX = "external_ids."
IDENTIFIERS_EMAILS = "identifiers.emails"
IDENTIFIERS_PHONES = "identifiers.phones"
KNOWN_EVIDENCE_FIELDS = frozenset({"domain", "linkedin", "name_normalized", "city"})
PERSON_KNOWN_EVIDENCE_FIELDS = frozenset(
    {
        "name_normalized",
        "linkedin",
        IDENTIFIERS_EMAILS,
        IDENTIFIERS_PHONES,
        "organization_id",
    }
)
RULE_EVIDENCE_KEYS = ("rule", "field", "value", "matched_value", "matched_record_id")
MANUAL_EVIDENCE_KEY = "decided_by"

FieldValue = Callable[[SourceRecord, str], Any]
RecordHas = Callable[[str, str, SourceRecord, frozenset[str]], bool]


@dataclass(frozen=True)
class LinkVerdict:
    """Whether an automatic link still holds, and why.

    `unverifiable` reasons keep the link (holds=True). Dropped links use
    value_changed or partner_changed. `kept` counters include unverifiable rows.
    """

    holds: bool
    reason: str


@dataclass(frozen=True)
class RecheckKind:
    """Per-entity matching fields and evidence checks for automatic links."""

    kind: str
    matching_fields: tuple[str, ...]
    automatic_methods: frozenset[str]
    known_fields: frozenset[str]
    value_of: FieldValue
    record_has: RecordHas


def _absent_string(value: str | None) -> str | None:
    """Treat blank strings as missing, matching cleaning output."""
    if value is None or value == "":
        return None
    return value


def _org_field_value(record: SourceRecord, name: str) -> Any:
    if name == "external_ids":
        return frozenset(external_keys(record))
    return _absent_string(getattr(record, name))


def _person_field_value(record: SourceRecord, name: str) -> Any:
    if name == "name_normalized":
        return _absent_string(record.name_normalized)
    if name == "linkedin":
        return frozenset(person_linkedins(record))
    if name == IDENTIFIERS_EMAILS:
        return frozenset(identifier_values(record, "emails"))
    if name == IDENTIFIERS_PHONES:
        return frozenset(identifier_values(record, "phones"))
    if name == "organization_id":
        return file_organization_id(record)
    return None


def _known_field(field: str, known: frozenset[str]) -> bool:
    if field in known:
        return True
    return field.startswith(EXTERNAL_IDS_PREFIX) and len(field) > len(EXTERNAL_IDS_PREFIX)


def _org_record_has(
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


def _person_record_has(
    field: str,
    value: str,
    record: SourceRecord,
    excluded_domains: frozenset[str],
) -> bool:
    del excluded_domains
    if field == "name_normalized":
        return bool(record.name_normalized) and record.name_normalized == value
    if field == "linkedin":
        return value in person_linkedins(record)
    if field == IDENTIFIERS_EMAILS:
        return value in identifier_values(record, "emails")
    if field == IDENTIFIERS_PHONES:
        return value in identifier_values(record, "phones")
    if field == "organization_id":
        return file_organization_id(record) == value
    return False


ORGANIZATION_RECHECK = RecheckKind(
    kind="organization",
    matching_fields=MATCHING_FIELDS,
    automatic_methods=AUTOMATIC_LINK_METHODS,
    known_fields=KNOWN_EVIDENCE_FIELDS,
    value_of=_org_field_value,
    record_has=_org_record_has,
)
PERSON_RECHECK = RecheckKind(
    kind="person",
    matching_fields=PERSON_MATCHING_FIELDS,
    automatic_methods=AUTOMATIC_PERSON_LINK_METHODS,
    known_fields=PERSON_KNOWN_EVIDENCE_FIELDS,
    value_of=_person_field_value,
    record_has=_person_record_has,
)
RECHECK_KINDS = (ORGANIZATION_RECHECK, PERSON_RECHECK)


def matching_changes(
    before: SourceRecord,
    after: SourceRecord,
    *,
    fields: Sequence[str] = MATCHING_FIELDS,
    value_of: FieldValue = _org_field_value,
) -> dict[str, dict[str, Any]]:
    """Return matching fields that differ between two versions.

    Shape matches record_diff.field_changes: {field: {"before": ..., "after": ...}}.
    Set-valued fields (external ids, person identifiers) compare as frozensets.
    """
    changes: dict[str, dict[str, Any]] = {}
    for name in fields:
        left = value_of(before, name)
        right = value_of(after, name)
        if left != right:
            changes[name] = {"before": left, "after": right}
    return changes


def _as_rule_entries(evidence: Sequence[Mapping[str, Any]]) -> list[RuleEvidence] | None:
    """Return rule evidence entries, or None when the payload is empty or malformed."""
    if not evidence:
        return None
    entries: list[RuleEvidence] = []
    for item in evidence:
        if not isinstance(item, Mapping) or MANUAL_EVIDENCE_KEY in item:
            return None
        if any(not isinstance(item.get(key), str) for key in RULE_EVIDENCE_KEYS):
            return None
        entries.append(cast(RuleEvidence, item))
    return entries


def _partner_supports(
    entries: Sequence[RuleEvidence],
    members: Sequence[SourceRecord],
    excluded_domains: frozenset[str],
    self_id: UUID,
    record_has: RecordHas,
) -> bool:
    """True when some other member carries every evidence matched_value, or none exist.

    Multi-entry rules need one partner that satisfies all entries together.
    """
    others = [member for member in members if member.id != self_id]
    if not others:
        return True
    return any(
        all(
            record_has(entry["field"], entry["matched_value"], member, excluded_domains)
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
    *,
    automatic_methods: frozenset[str] = AUTOMATIC_LINK_METHODS,
    known_fields: frozenset[str] = KNOWN_EVIDENCE_FIELDS,
    record_has: RecordHas = _org_record_has,
) -> LinkVerdict:
    """Decide whether automatic-link evidence still holds for `record`.

    Self side: every entry's value is still on this record and eligible.
    Partner side: one other current member carries every matched_value, or there is no
    other member to contradict. Empty or malformed evidence is kept as unverifiable.
    """
    if link_method not in automatic_methods:
        return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)

    entries = _as_rule_entries(evidence)
    if entries is None:
        return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)

    for entry in entries:
        field = entry["field"]
        rule = entry["rule"]
        if rule != link_method or not _known_field(field, known_fields):
            return LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE)
        if not record_has(field, entry["value"], record, excluded_domains):
            return LinkVerdict(holds=False, reason=REASON_VALUE_CHANGED)

    if not _partner_supports(entries, tuple(members), excluded_domains, record.id, record_has):
        return LinkVerdict(holds=False, reason=REASON_PARTNER_CHANGED)

    return LinkVerdict(holds=True, reason=REASON_HOLDS)

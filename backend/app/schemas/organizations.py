# AI contribution: 50% or more AI-generated
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RecordRef(BaseModel):
    """What links back to a source record: /records/{source_id}/{external_id}."""

    source_id: uuid.UUID
    external_id: str
    # The version this item is about.
    version_id: uuid.UUID
    label: str


class OrganizationSummaryOut(BaseModel):
    # None for a record that matched no other; follow `record` instead.
    organization_id: uuid.UUID | None
    record: RecordRef | None
    name: str
    records: int
    last_changed: datetime


class LinkOut(BaseModel):
    method: str
    confidence: float
    decided_by: str
    decided_at: datetime
    evidence: list[dict[str, Any]]


class OrganizationVersionOut(BaseModel):
    id: uuid.UUID
    label: str
    domain: str | None
    linkedin: str | None
    valid_from: datetime
    valid_to: datetime | None


class LinkedRecordOut(BaseModel):
    record: RecordRef
    link: LinkOut
    versions: list[OrganizationVersionOut]


class OrganizationOut(BaseModel):
    id: uuid.UUID
    display_name: str
    primary_domain: str | None
    records: list[LinkedRecordOut]


class TimelineEntryOut(BaseModel):
    sync_run_id: uuid.UUID
    started_at: datetime
    change_type: str
    record: RecordRef
    changed_fields: dict[str, Any]


class FieldValueOut(BaseModel):
    field: str
    # As stored in the source, and as PULSE normalized it for matching.
    value: str | None
    normalized: str | None


class MatchCandidateOut(BaseModel):
    id: uuid.UUID
    score: float
    status: str
    left: RecordRef
    right: RecordRef
    matched_fields: list[dict[str, Any]]
    left_values: list[FieldValueOut] = []
    right_values: list[FieldValueOut] = []
    decided_by: str | None = None
    decided_at: datetime | None = None

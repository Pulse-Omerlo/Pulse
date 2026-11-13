# AI contribution: 50% or more AI-generated
"""Which organization a person works for, and the signals that say so."""

import uuid
from datetime import datetime
from typing import TypedDict

from sqlalchemy import CheckConstraint, DateTime, Double, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_column, one_of, same_tenant_fk, tenant_id_column, uuid_pk

# Strongest first: a relation the network recorded, then a work email, then the job title.
AFFILIATION_METHODS = ("relation", "email_domain", "job_title")
# A conflict is a candidate set aside for review because the signals disagree.
AFFILIATION_STATUSES = ("linked", "conflict")


class AffiliationEvidence(TypedDict):
    # The affiliation method this value supports.
    signal: str
    # What matched: an organization id, a domain, or a normalized organization name.
    value: str
    # Where the person record holds it: the field and its value (e.g. the email address).
    field: str
    source: str


class Affiliation(Base):
    """A person record version linked to an organization record version.

    Both sides are current versions when the row is written. Affiliations are worked
    out again after each sync, so a new version of either record is checked again;
    rows of closed person versions are kept as history.
    """

    __tablename__ = "affiliation"
    __table_args__ = (
        UniqueConstraint("tenant_id", "person_record_id", "organization_record_id"),
        same_tenant_fk("person_record_id", "source_record"),
        same_tenant_fk("organization_record_id", "source_record"),
        CheckConstraint(one_of("method", AFFILIATION_METHODS), name="method"),
        CheckConstraint(one_of("status", AFFILIATION_STATUSES), name="status"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_is_array"),
        Index("ix_affiliation_organization_record", "tenant_id", "organization_record_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    person_record_id: Mapped[uuid.UUID]
    organization_record_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Double)
    evidence: Mapped[list[AffiliationEvidence]] = json_column("[]")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

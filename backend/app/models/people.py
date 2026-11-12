# AI contribution: 50% or more AI-generated
"""Resolved people and the links that assign source-record versions to them."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Double, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_column, one_of, same_tenant_fk, tenant_id_column, uuid_pk
from app.models.resolution import ManualEvidence, RuleEvidence

PERSON_LINK_METHODS = ("email", "phone", "linkedin", "organization_id", "manual")
AUTOMATIC_PERSON_LINK_METHODS = frozenset(
    method for method in PERSON_LINK_METHODS if method != "manual"
)


class Person(Base):
    """What PULSE believes is one real-world person."""

    __tablename__ = "person"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    display_name: Mapped[str] = mapped_column(Text)


class PersonLink(Base):
    """The decision that a source record version belongs to a person.

    The version is the primary key, so each version belongs to at most one person.
    A new version starts unlinked: versioning must copy the link to it in the same
    transaction that closes the old version. Current membership joins source_record on
    valid_to IS NULL. Automatic links are re-checked when matching fields change on a
    new version; the old version keeps its link.
    """

    __tablename__ = "person_link"
    __table_args__ = (
        same_tenant_fk("person_id", "person"),
        same_tenant_fk("source_record_id", "source_record"),
        CheckConstraint(one_of("method", PERSON_LINK_METHODS), name="method"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_is_array"),
        Index("ix_person_link_person", "tenant_id", "person_id"),
    )

    source_record_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    person_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    method: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Double)
    decided_by: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    evidence: Mapped[list[RuleEvidence | ManualEvidence]] = json_column("[]")

# AI contribution: 50% or more AI-generated
import uuid
from datetime import datetime
from typing import TypedDict

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_column, one_of, same_tenant_fk, tenant_id_column

CLASSIFICATION_STATUSES = ("classified", "unclassified")


class SectorEvidence(TypedDict):
    # "category", "name" or "description", and the category id or keyword found there.
    field: str
    value: str


class SectorEntry(TypedDict):
    code: str
    method: str
    evidence: list[SectorEvidence]


class IndustryClassification(Base):
    """The NAICS sectors of one organization source record version.

    Keyed by the version like OrgLink, but never copied to a new version: each version is
    classified on its own, so a change to its categories, name or description is picked up.
    """

    __tablename__ = "industry_classification"
    __table_args__ = (
        same_tenant_fk("source_record_id", "source_record"),
        CheckConstraint(one_of("status", CLASSIFICATION_STATUSES), name="status"),
        CheckConstraint(
            "(status = 'classified') = (primary_sector IS NOT NULL)",
            name="primary_when_classified",
        ),
        CheckConstraint("jsonb_typeof(sectors) = 'array'", name="sectors_is_array"),
        # Counting a network's organizations by sector.
        Index("ix_industry_classification_primary_sector", "tenant_id", "primary_sector"),
    )

    source_record_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    status: Mapped[str] = mapped_column(Text)
    primary_sector: Mapped[str | None] = mapped_column(Text)
    sectors: Mapped[list[SectorEntry]] = json_column("[]")
    reason: Mapped[str | None] = mapped_column(Text)
    # Rules the result came from; a different version means the record is classified again.
    rules_version: Mapped[str] = mapped_column(Text)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

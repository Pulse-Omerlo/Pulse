# AI contribution: 50% or more AI-generated
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_column, one_of, same_tenant_fk, tenant_id_column, uuid_pk
from app.processing.sector_mix import PEOPLE_STATUSES


class SectorMixResult(Base):
    """A network's sector mix and gaps as computed after one sync run.

    One row per run, so a later run can be compared with an earlier one. Each sector in
    `sectors` lists the organizations, their record versions and the people it counts.
    """

    __tablename__ = "sector_mix"
    __table_args__ = (
        # One result per run; also indexes the sync_run foreign key.
        UniqueConstraint("tenant_id", "sync_run_id"),
        same_tenant_fk("sync_run_id", "sync_run"),
        CheckConstraint(one_of("people_status", PEOPLE_STATUSES), name="people_status"),
        CheckConstraint("classified <= organizations", name="classified_within_total"),
        CheckConstraint("jsonb_typeof(sectors) = 'array'", name="sectors_is_array"),
        CheckConstraint("jsonb_typeof(gaps) = 'array'", name="gaps_is_array"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    sync_run_id: Mapped[uuid.UUID]
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The gap threshold and targets used, with their version.
    settings: Mapped[dict] = json_column()
    organizations: Mapped[int] = mapped_column(Integer)
    classified: Mapped[int] = mapped_column(Integer)
    people_status: Mapped[str] = mapped_column(Text)
    people: Mapped[int] = mapped_column(Integer)
    sectors: Mapped[list[dict]] = json_column("[]")
    gaps: Mapped[list[dict]] = json_column("[]")
    # Organizations with no sector and people placed in none, so the totals add up.
    unclassified: Mapped[list[dict]] = json_column("[]")
    people_unplaced: Mapped[list[dict]] = json_column("[]")

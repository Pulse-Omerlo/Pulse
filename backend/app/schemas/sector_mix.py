# AI contribution: 50% or more AI-generated
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.organizations import RecordRef


class MixOrganizationOut(BaseModel):
    # The matched organization's id, or the record version's id when it matched none.
    key: str
    name: str
    records: list[str]
    # Where to open it: the organization page, or the record when it matched none.
    organization_id: str | None = None
    record: RecordRef | None = None


class MixPersonOut(BaseModel):
    id: str
    name: str
    organization: str | None


class SectorShareOut(BaseModel):
    code: str
    name: str
    organization_count: int
    # Percent of the classified organizations.
    organization_share: float
    # None while people are not linked to organizations.
    people_count: int | None
    people_share: float | None
    target: float | None
    # Percent of the region's classified employer businesses; None when not compared.
    region_share: float | None = None
    organizations: list[MixOrganizationOut]
    people: list[MixPersonOut] | None


class GapOut(BaseModel):
    code: str
    kind: str
    # Percentage points.
    size: float
    explanation: str


class BaselineOut(BaseModel):
    table: str
    title: str
    release: str
    reference_period: str
    attribution: str
    licence: str
    region: str
    region_name: str
    classified_businesses: int
    # Left out of every regional share.
    unclassified_businesses: int


class MixSettingsOut(BaseModel):
    gap_points: float
    targets: dict[str, float]
    region: str | None = None
    region_margin: float | None = None
    baseline: BaselineOut | None = None
    version: str


class SectorMixOut(BaseModel):
    sync_run_id: uuid.UUID
    computed_at: datetime
    settings: MixSettingsOut
    organizations: int
    classified: int
    people_status: str
    people: int
    sectors: list[SectorShareOut]
    gaps: list[GapOut]
    unclassified: list[MixOrganizationOut]
    people_unplaced: list[MixPersonOut]

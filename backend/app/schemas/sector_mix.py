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
    organizations: list[MixOrganizationOut]
    people: list[MixPersonOut] | None


class GapOut(BaseModel):
    code: str
    kind: str
    # Percentage points.
    size: float
    explanation: str


class MixSettingsOut(BaseModel):
    gap_points: float
    targets: dict[str, float]
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

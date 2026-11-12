# AI contribution: 50% or more AI-generated
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.organizations import RecordRef


class AffiliationEvidenceOut(BaseModel):
    signal: str
    value: str
    field: str
    source: str


class AffiliationOut(BaseModel):
    id: uuid.UUID
    person: RecordRef
    organization: RecordRef
    status: str
    method: str
    confidence: float
    evidence: list[AffiliationEvidenceOut]
    decided_at: datetime

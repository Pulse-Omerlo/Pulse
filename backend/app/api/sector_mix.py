# AI contribution: 50% or more AI-generated
"""A network's sector mix and gaps, as computed after its sync runs."""

import uuid

from fastapi import APIRouter

from app.api.browse import TenantDb
from app.core.errors import NotFoundError
from app.schemas.sector_mix import SectorMixOut
from app.services import browse
from app.services.sector_mix import latest_mix, mix_for_run

router = APIRouter(prefix="/api/tenants", tags=["sector mix"])


@router.get("/{tenant_id}/sector-mix", response_model=SectorMixOut)
def latest_sector_mix(tenant_id: uuid.UUID, db: TenantDb):
    browse.get_tenant(db, tenant_id)
    result = latest_mix(db, tenant_id)
    if result is None:
        raise NotFoundError("No sector mix yet: it is computed after a successful sync")
    return SectorMixOut.model_validate(result, from_attributes=True)


@router.get("/{tenant_id}/runs/{run_id}/sector-mix", response_model=SectorMixOut)
def run_sector_mix(tenant_id: uuid.UUID, run_id: uuid.UUID, db: TenantDb):
    browse.get_tenant(db, tenant_id)
    result = mix_for_run(db, tenant_id, run_id)
    if result is None:
        raise NotFoundError("No sector mix for this run")
    return SectorMixOut.model_validate(result, from_attributes=True)

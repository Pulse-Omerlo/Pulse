# AI contribution: 50% or more AI-generated
"""A network's sector mix and gaps, as computed after its sync runs."""

import uuid

from fastapi import APIRouter

from app.api.browse import TenantDb
from app.api.organizations import ref
from app.core.errors import NotFoundError
from app.schemas.sector_mix import SectorMixOut
from app.services import browse
from app.services.sector_mix import latest_mix, mix_for_run, records_by_id

router = APIRouter(prefix="/api/tenants", tags=["sector mix"])


@router.get("/{tenant_id}/sector-mix", response_model=SectorMixOut)
def latest_sector_mix(tenant_id: uuid.UUID, db: TenantDb):
    browse.get_tenant(db, tenant_id)
    result = latest_mix(db, tenant_id)
    if result is None:
        raise NotFoundError("No sector mix yet: it is computed after a successful sync")
    return with_links(db, tenant_id, SectorMixOut.model_validate(result, from_attributes=True))


@router.get("/{tenant_id}/runs/{run_id}/sector-mix", response_model=SectorMixOut)
def run_sector_mix(tenant_id: uuid.UUID, run_id: uuid.UUID, db: TenantDb):
    browse.get_tenant(db, tenant_id)
    result = mix_for_run(db, tenant_id, run_id)
    if result is None:
        raise NotFoundError("No sector mix for this run")
    return with_links(db, tenant_id, SectorMixOut.model_validate(result, from_attributes=True))


def with_links(db, tenant_id: uuid.UUID, mix: SectorMixOut) -> SectorMixOut:
    """Point each organization at its page, or at its record when it matched no other."""
    organizations = [o for s in mix.sectors for o in s.organizations] + mix.unclassified
    unmatched = [o for o in organizations if o.key in o.records]
    records = records_by_id(db, tenant_id, [uuid.UUID(o.key) for o in unmatched])
    for organization in organizations:
        if organization.key not in organization.records:
            organization.organization_id = organization.key
        elif record := records.get(organization.key):
            organization.record = ref(record)
    return mix

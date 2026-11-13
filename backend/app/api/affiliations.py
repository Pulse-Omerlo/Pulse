# AI contribution: 50% or more AI-generated
"""The organizations a network's people work for, and the conflicts set aside for review."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.browse import TenantDb
from app.api.organizations import ref
from app.api.pagination import PageParams
from app.models.affiliation import AFFILIATION_STATUSES
from app.schemas.affiliations import AffiliationOut
from app.schemas.pagination import Page
from app.services import browse
from app.services.affiliations import affiliations_page

router = APIRouter(prefix="/api/tenants", tags=["affiliations"])

Status = Annotated[str, Query(pattern="^(" + "|".join(AFFILIATION_STATUSES) + ")$")]


@router.get("/{tenant_id}/affiliations", response_model=Page[AffiliationOut])
def affiliations(tenant_id: uuid.UUID, db: TenantDb, paging: PageParams, status: Status = "linked"):
    """People linked to an organization, or with `status=conflict` the ones to review."""
    browse.get_tenant(db, tenant_id)
    rows, total = affiliations_page(db, tenant_id, paging, status=status)
    return Page(
        items=[
            AffiliationOut(
                id=row.id,
                person=ref(person),
                organization=ref(organization),
                status=row.status,
                method=row.method,
                confidence=row.confidence,
                evidence=row.evidence,
                decided_at=row.decided_at,
            )
            for row, person, organization in rows
        ],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )

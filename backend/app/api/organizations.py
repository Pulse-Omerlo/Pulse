# AI contribution: 50% or more AI-generated
"""Read-only endpoints for a tenant's organizations, their timeline and likely duplicates."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.browse import TenantDb
from app.api.pagination import PageParams
from app.models import MatchCandidate, SourceRecord
from app.models.resolution import CANDIDATE_STATUSES
from app.schemas.organizations import (
    LinkedRecordOut,
    LinkOut,
    MatchCandidateOut,
    OrganizationOut,
    OrganizationSummaryOut,
    OrganizationVersionOut,
    RecordRef,
    TimelineEntryOut,
)
from app.schemas.pagination import Page
from app.services import browse
from app.services import organization_views as views

router = APIRouter(prefix="/api/tenants", tags=["organizations"])

Sort = Annotated[Literal[views.ORGANIZATION_SORTS], Query()]  # type: ignore[valid-type]
Order = Annotated[Literal["asc", "desc"], Query()]
Status = Annotated[str, Query(pattern="^(" + "|".join(CANDIDATE_STATUSES) + ")$")]


def ref(record: SourceRecord) -> RecordRef:
    return RecordRef(
        source_id=record.source_id,
        external_id=record.external_id,
        version_id=record.id,
        label=browse.label(record),
    )


def organization_out(organization, records: list[views.LinkedRecord]) -> OrganizationOut:
    return OrganizationOut(
        id=organization.id,
        display_name=organization.display_name,
        primary_domain=organization.primary_domain,
        records=[
            LinkedRecordOut(
                record=ref(linked.current),
                link=LinkOut.model_validate(linked.link, from_attributes=True),
                versions=[
                    OrganizationVersionOut(
                        id=v.id,
                        label=browse.label(v),
                        domain=v.domain,
                        linkedin=v.linkedin,
                        valid_from=v.valid_from,
                        valid_to=v.valid_to,
                    )
                    for v in linked.versions
                ],
            )
            for linked in records
        ],
    )


@router.get("/{tenant_id}/organizations", response_model=Page[OrganizationSummaryOut])
def list_organizations(
    tenant_id: uuid.UUID,
    db: TenantDb,
    paging: PageParams,
    sort: Sort = "name",
    order: Order = "asc",
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    """Matched organizations and the records that matched none, one page at a time."""
    browse.get_tenant(db, tenant_id)
    rows, total = views.list_organizations(
        db, tenant_id, paging, sort=sort, descending=order == "desc", query=q
    )
    return Page(
        items=[
            OrganizationSummaryOut(
                organization_id=row.organization_id,
                record=(
                    None
                    if row.organization_id
                    else RecordRef(
                        source_id=row.source_id,
                        external_id=row.external_id,
                        version_id=row.record_id,
                        label=row.name,
                    )
                ),
                name=row.name,
                records=row.records,
                last_changed=row.last_changed,
            )
            for row in rows
        ],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{tenant_id}/organizations/{organization_id}", response_model=OrganizationOut)
def organization_detail(tenant_id: uuid.UUID, organization_id: uuid.UUID, db: TenantDb):
    """An organization with its current records, how each was linked, and their versions."""
    organization = views.get_organization(db, tenant_id, organization_id)
    records = views.linked_records(db, tenant_id, [organization.id])
    return organization_out(organization, records[organization.id])


@router.get(
    "/{tenant_id}/organizations/{organization_id}/timeline",
    response_model=Page[TimelineEntryOut],
)
def organization_timeline(
    tenant_id: uuid.UUID, organization_id: uuid.UUID, db: TenantDb, paging: PageParams
):
    """Changes to the organization's records found by sync runs, newest first."""
    organization = views.get_organization(db, tenant_id, organization_id)
    rows, total = views.organization_timeline(db, tenant_id, organization.id, paging)
    return Page(
        items=[
            TimelineEntryOut(
                sync_run_id=change.sync_run_id,
                started_at=started_at,
                change_type=change.change_type,
                record=ref(record),
                changed_fields=change.changed_fields,
            )
            for change, record, started_at in rows
        ],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{tenant_id}/duplicates/candidates", response_model=Page[MatchCandidateOut])
def duplicate_candidates(
    tenant_id: uuid.UUID, db: TenantDb, paging: PageParams, status: Status = "pending"
):
    """Pairs of records that may be the same organization, set aside for review."""
    browse.get_tenant(db, tenant_id)
    rows, total = views.match_candidates(db, tenant_id, paging, status=status)
    return Page(
        items=[candidate_out(candidate, left, right) for candidate, left, right in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


def candidate_out(
    candidate: MatchCandidate, left: SourceRecord, right: SourceRecord
) -> MatchCandidateOut:
    return MatchCandidateOut(
        id=candidate.id,
        score=candidate.score,
        status=candidate.status,
        left=ref(left),
        right=ref(right),
        matched_fields=candidate.matched_fields,
    )


@router.get("/{tenant_id}/duplicates/groups", response_model=Page[OrganizationOut])
def duplicate_groups(tenant_id: uuid.UUID, db: TenantDb, paging: PageParams):
    """Organizations matched from two or more records, with the evidence of each link."""
    browse.get_tenant(db, tenant_id)
    groups, total = views.matched_groups(db, tenant_id, paging)
    return Page(
        items=[organization_out(organization, records) for organization, records in groups],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )

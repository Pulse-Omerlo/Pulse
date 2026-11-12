# AI contribution: 50% or more AI-generated
"""Read-only endpoints for the sync runs page: runs, their changes and record history."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.dependencies import CurrentUser, Db, TenantDb, database, tenant_database
from app.api.pagination import PageParams
from app.core.tenancy import system_access
from app.models.changes import CHANGE_TYPES
from app.models.records import ENTITY_KINDS
from app.schemas.browse import (
    ChangeOut,
    RecordHistoryOut,
    RecordOut,
    SyncRunOut,
    TenantOut,
    VersionEvent,
    VersionOut,
)
from app.schemas.pagination import Page
from app.services import browse

router = APIRouter(prefix="/api/tenants", tags=["browse"])


# Re-exported: routes and tests import these from here.
__all__ = ["Db", "TenantDb", "database", "router", "tenant_database"]

ChangeType = Annotated[str | None, Query(pattern="^(" + "|".join(CHANGE_TYPES) + ")$")]
Kind = Annotated[str | None, Query(pattern="^(" + "|".join(ENTITY_KINDS) + ")$")]
ExternalId = Annotated[str, Path(min_length=1, max_length=200)]


@router.get("", response_model=list[TenantOut])
def list_tenants(db: Db, user: CurrentUser):
    """The tenants the signed-in user can open: their own."""
    with system_access(db):
        return [
            TenantOut(id=t.id, name=t.name, slug=t.omerlo_media_id)
            for t in browse.list_tenants(db)
            if t.id == user.tenant_id
        ]


@router.get("/{tenant_id}/runs", response_model=Page[SyncRunOut])
def list_runs(tenant_id: uuid.UUID, db: TenantDb, paging: PageParams):
    browse.get_tenant(db, tenant_id)
    runs, total = browse.list_runs(db, tenant_id, paging)
    return Page(
        items=[SyncRunOut.model_validate(run, from_attributes=True) for run in runs],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{tenant_id}/runs/{run_id}/changes", response_model=Page[ChangeOut])
def run_changes(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    db: TenantDb,
    paging: PageParams,
    change_type: ChangeType = None,
):
    browse.get_tenant(db, tenant_id)
    rows, total = browse.run_changes(db, tenant_id, run_id, paging, change_type=change_type)
    items = [
        ChangeOut(
            change_type=change.change_type,
            kind=record.kind,
            source_id=record.source_id,
            external_id=record.external_id,
            label=browse.label(record),
            changed_fields=change.changed_fields,
        )
        for change, record in rows
    ]
    return Page(items=items, total=total, page=paging.page, page_size=paging.page_size)


@router.get("/{tenant_id}/records", response_model=list[RecordOut])
def search_records(
    tenant_id: uuid.UUID,
    db: TenantDb,
    kind: Kind = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    browse.get_tenant(db, tenant_id)
    return [
        RecordOut(
            source_id=r.source_id,
            external_id=r.external_id,
            kind=r.kind,
            label=browse.label(r),
            domain=r.domain,
            linkedin=r.linkedin,
            valid_from=r.valid_from,
        )
        for r in browse.search_records(db, tenant_id, kind=kind, query=q)
    ]


@router.get("/{tenant_id}/records/{source_id}/{external_id}", response_model=RecordHistoryOut)
def record_history(
    tenant_id: uuid.UUID, source_id: uuid.UUID, external_id: ExternalId, db: TenantDb
):
    browse.get_tenant(db, tenant_id)
    versions, changes = browse.record_history(db, tenant_id, source_id, external_id)
    started = browse.run_order(db, {c.sync_run_id for cs in changes.values() for c in cs})
    return RecordHistoryOut(
        source_id=source_id,
        external_id=external_id,
        kind=versions[-1].kind,
        versions=[
            VersionOut(
                id=v.id,
                label=browse.label(v),
                name_normalized=v.name_normalized,
                domain=v.domain,
                linkedin=v.linkedin,
                external_ids=v.external_ids,
                valid_from=v.valid_from,
                valid_to=v.valid_to,
                first_seen_run=v.first_seen_run,
                removed_run=v.removed_run,
                events=[
                    VersionEvent(
                        sync_run_id=c.sync_run_id,
                        change_type=c.change_type,
                        changed_fields=c.changed_fields,
                    )
                    for c in sorted(changes.get(v.id, []), key=lambda c: started[c.sync_run_id])
                ],
            )
            for v in versions
        ],
    )

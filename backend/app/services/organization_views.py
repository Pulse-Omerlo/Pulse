# AI contribution: 50% or more AI-generated
"""Read-only views of a tenant's organizations, their records, timeline and likely duplicates.

Each function runs a fixed number of queries whatever the page holds, and filters on
tenant_id itself on top of the tenant session's own filter.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, case, func, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.core.errors import NotFoundError
from app.models import MatchCandidate, Organization, OrgLink, RecordChange, SourceRecord, SyncRun
from app.services.pagination import Paging

ORGANIZATION_SORTS = ("name", "records", "last_changed")


@dataclass(frozen=True)
class OrganizationRow:
    """A matched organization, or a current record that matched none."""

    organization_id: uuid.UUID | None
    # The record to link to when organization_id is None.
    record_id: uuid.UUID | None
    source_id: uuid.UUID | None
    external_id: str | None
    name: str
    records: int
    last_changed: datetime


@dataclass(frozen=True)
class LinkedRecord:
    current: SourceRecord
    link: OrgLink
    # Every version of the record, oldest first.
    versions: list[SourceRecord]


def current_organization(tenant_id: uuid.UUID) -> ColumnElement[bool]:
    return (
        (SourceRecord.tenant_id == tenant_id)
        & (SourceRecord.kind == "organization")
        & SourceRecord.valid_to.is_(None)
    )


def link_join() -> ColumnElement[bool]:
    return (OrgLink.tenant_id == SourceRecord.tenant_id) & (
        OrgLink.source_record_id == SourceRecord.id
    )


def list_organizations(
    session: Session,
    tenant_id: uuid.UUID,
    paging: Paging,
    *,
    sort: str = "name",
    descending: bool = False,
    query: str | None = None,
) -> tuple[list[OrganizationRow], int]:
    """One page of organizations and the total, in two queries."""
    unmatched = OrgLink.organization_id.is_(None)
    record_id = case((unmatched, SourceRecord.id))
    source_id = case((unmatched, SourceRecord.source_id))
    external_id = case((unmatched, SourceRecord.external_id))
    name = func.coalesce(
        Organization.display_name,
        SourceRecord.attributes["name"].astext,
        SourceRecord.name_normalized,
        SourceRecord.external_id,
    )
    where = [current_organization(tenant_id)]
    if query and query.strip():
        where.append(name.ilike(f"%{query.strip()}%"))

    def joined(statement):
        return (
            statement.outerjoin(OrgLink, link_join())
            .outerjoin(
                Organization,
                (Organization.tenant_id == OrgLink.tenant_id)
                & (Organization.id == OrgLink.organization_id),
            )
            .where(*where)
        )

    organization_key = func.coalesce(OrgLink.organization_id, SourceRecord.id)
    records = func.count(SourceRecord.id)
    last_changed = func.max(SourceRecord.valid_from)
    sort_key = {
        "name": func.lower(func.max(name)),
        "records": records,
        "last_changed": last_changed,
    }[sort]
    statement = (
        joined(
            select(
                OrgLink.organization_id,
                record_id,
                source_id,
                external_id,
                func.max(name),
                records,
                last_changed,
            ).select_from(SourceRecord)
        )
        .group_by(OrgLink.organization_id, record_id, source_id, external_id)
        .order_by(
            sort_key.desc() if descending else sort_key,
            func.coalesce(OrgLink.organization_id, record_id),
        )
        .limit(paging.page_size)
        .offset(paging.offset)
    )
    rows = [OrganizationRow(*row) for row in session.execute(statement)]
    total = session.scalar(
        joined(select(func.count(func.distinct(organization_key))).select_from(SourceRecord))
    )
    return rows, total or 0


def get_organization(
    session: Session, tenant_id: uuid.UUID, organization_id: uuid.UUID
) -> Organization:
    """The organization, or the one it was merged into, so an old id still resolves."""
    seen = set()
    while organization_id not in seen:
        seen.add(organization_id)
        organization = session.scalar(
            select(Organization).where(
                Organization.tenant_id == tenant_id, Organization.id == organization_id
            )
        )
        if organization is None:
            raise NotFoundError("Organization not found")
        if organization.merged_into is None:
            return organization
        organization_id = organization.merged_into
    raise ValueError(f"Organization {organization_id} is merged in a loop")


def linked_records(
    session: Session, tenant_id: uuid.UUID, organization_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[LinkedRecord]]:
    """The current records of each organization with their link and all their versions.

    Two queries for any number of organizations and records.
    """
    if not organization_ids:
        return {}
    current = session.execute(
        select(SourceRecord, OrgLink)
        .join(OrgLink, link_join())
        .where(current_organization(tenant_id), OrgLink.organization_id.in_(organization_ids))
        .order_by(SourceRecord.source_id, SourceRecord.external_id)
    ).all()
    keys = [(record.source_id, record.external_id) for record, _ in current]
    versions: dict[tuple[uuid.UUID, str], list[SourceRecord]] = {key: [] for key in keys}
    if keys:
        for version in session.scalars(
            select(SourceRecord)
            .where(
                SourceRecord.tenant_id == tenant_id,
                tuple_(SourceRecord.source_id, SourceRecord.external_id).in_(keys),
            )
            .order_by(SourceRecord.valid_from)
        ):
            versions[(version.source_id, version.external_id)].append(version)
    result: dict[uuid.UUID, list[LinkedRecord]] = {i: [] for i in organization_ids}
    for record, link in current:
        result[link.organization_id].append(
            LinkedRecord(record, link, versions[(record.source_id, record.external_id)])
        )
    return result


def organization_timeline(
    session: Session, tenant_id: uuid.UUID, organization_id: uuid.UUID, paging: Paging
) -> tuple[list[tuple[RecordChange, SourceRecord, datetime]], int]:
    """Changes to every version of the organization's current records, newest run first."""
    members = (
        select(SourceRecord.source_id, SourceRecord.external_id)
        .join(OrgLink, link_join())
        .where(current_organization(tenant_id), OrgLink.organization_id == organization_id)
    )
    where = (
        RecordChange.tenant_id == tenant_id,
        tuple_(SourceRecord.source_id, SourceRecord.external_id).in_(members),
    )
    on_version = (SourceRecord.tenant_id == RecordChange.tenant_id) & (
        SourceRecord.id == RecordChange.source_record_id
    )
    on_run = (SyncRun.tenant_id == RecordChange.tenant_id) & (
        SyncRun.id == RecordChange.sync_run_id
    )
    rows = session.execute(
        select(RecordChange, SourceRecord, SyncRun.started_at)
        .join(SourceRecord, on_version)
        .join(SyncRun, on_run)
        .where(*where)
        .order_by(SyncRun.started_at.desc(), RecordChange.id)
        .limit(paging.page_size)
        .offset(paging.offset)
    ).all()
    total = session.scalar(
        select(func.count(RecordChange.id)).join(SourceRecord, on_version).where(*where)
    )
    return [(change, record, started) for change, record, started in rows], total or 0


def match_candidates(
    session: Session, tenant_id: uuid.UUID, paging: Paging, *, status: str = "pending"
) -> tuple[list[tuple[MatchCandidate, SourceRecord, SourceRecord]], int]:
    """Possible matches set aside for review, highest score first, with both records."""
    left, right = aliased(SourceRecord), aliased(SourceRecord)
    where = (MatchCandidate.tenant_id == tenant_id, MatchCandidate.status == status)
    rows = session.execute(
        select(MatchCandidate, left, right)
        .join(
            left,
            (left.tenant_id == MatchCandidate.tenant_id)
            & (left.id == MatchCandidate.left_record_id),
        )
        .join(
            right,
            (right.tenant_id == MatchCandidate.tenant_id)
            & (right.id == MatchCandidate.right_record_id),
        )
        .where(*where)
        .order_by(MatchCandidate.score.desc(), MatchCandidate.id)
        .limit(paging.page_size)
        .offset(paging.offset)
    ).all()
    total = session.scalar(select(func.count(MatchCandidate.id)).where(*where))
    return [(c, lr, rr) for c, lr, rr in rows], total or 0


def matched_groups(
    session: Session, tenant_id: uuid.UUID, paging: Paging
) -> tuple[list[tuple[Organization, list[LinkedRecord]]], int]:
    """Organizations that Tier 1 matching formed from two or more current records."""
    size = func.count(SourceRecord.id)

    def grouped(*columns):
        # Postgres allows the organization's columns, since they depend on its id.
        return (
            select(*columns)
            .join(
                OrgLink,
                (OrgLink.tenant_id == Organization.tenant_id)
                & (OrgLink.organization_id == Organization.id),
            )
            .join(SourceRecord, link_join())
            .where(Organization.tenant_id == tenant_id, current_organization(tenant_id))
            .group_by(Organization.id)
            .having(size >= 2)
        )

    page = session.scalars(
        grouped(Organization)
        .order_by(size.desc(), func.lower(Organization.display_name), Organization.id)
        .limit(paging.page_size)
        .offset(paging.offset)
    ).all()
    # Groups are few next to records, so counting their ids is cheap.
    total = len(session.scalars(grouped(Organization.id)).all())
    records = linked_records(session, tenant_id, [o.id for o in page])
    return [(organization, records[organization.id]) for organization in page], total

# AI contribution: Below 50% AI-generated

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import Organization, OrgLink, SourceRecord


def current_records(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
) -> list[SourceRecord]:
    """Current organization members: linked source_record versions with valid_to IS NULL.

    A merged organization's id lists the members of the organization it was merged into.
    """
    with bound_to_tenant(session, tenant_id):
        organization_id = surviving_organization_id(session, tenant_id, organization_id)
        if organization_id is None:
            return []
        return list(
            session.scalars(
                select(SourceRecord)
                .join(
                    OrgLink,
                    OrgLink.source_record_id == SourceRecord.id,
                )
                .where(
                    SourceRecord.tenant_id == tenant_id,
                    OrgLink.tenant_id == tenant_id,
                    OrgLink.organization_id == organization_id,
                    SourceRecord.kind == "organization",
                    SourceRecord.valid_to.is_(None),
                )
                .order_by(SourceRecord.id)
            )
        )


def surviving_organization_id(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
) -> UUID | None:
    """The id an organization now goes by after merges, or None if the tenant has no such id."""
    with bound_to_tenant(session, tenant_id):
        organization = surviving_organization(session, tenant_id, organization_id)
    return organization.id if organization else None


def surviving_organization(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
) -> Organization | None:
    """The organization an id now stands for after merges, or None if the tenant has no such id.

    Uses the caller's tenant scope and checks tenant_id itself on top of it.
    """
    seen = set()
    while organization_id not in seen:
        seen.add(organization_id)
        organization = session.get(Organization, organization_id)
        if organization is None or organization.tenant_id != tenant_id:
            return None
        if organization.merged_into is None:
            return organization
        organization_id = organization.merged_into
    raise ValueError(f"Organization {organization_id} is merged in a loop")


def members_as_of(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
    moment: datetime,
) -> list[SourceRecord]:
    """Organization members whose linked versions were valid at `moment`."""
    with bound_to_tenant(session, tenant_id):
        return list(
            session.scalars(
                select(SourceRecord)
                .join(OrgLink, OrgLink.source_record_id == SourceRecord.id)
                .where(
                    SourceRecord.tenant_id == tenant_id,
                    OrgLink.tenant_id == tenant_id,
                    OrgLink.organization_id == organization_id,
                    SourceRecord.kind == "organization",
                    SourceRecord.valid_from <= moment,
                    or_(SourceRecord.valid_to.is_(None), SourceRecord.valid_to > moment),
                )
                .order_by(SourceRecord.id)
            )
        )

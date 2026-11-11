# AI contribution: Below 50% AI-generated

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import Organization, OrgLink, SourceRecord


# This service retrieves the current records belonging to an organization.
# It joins records through their organization links, checks the tenant on both tables, and excludes
# historical versions so each logical record's current membership is shown
def current_records(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
) -> list[SourceRecord]:
    with bound_to_tenant(session, tenant_id):
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
        seen = set()
        while organization_id not in seen:
            seen.add(organization_id)
            organization = session.get(Organization, organization_id)
            if organization is None:
                return None
            if organization.merged_into is None:
                return organization.id
            organization_id = organization.merged_into
        raise ValueError(f"Organization {organization_id} is merged in a loop")

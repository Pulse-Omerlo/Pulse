# AI declaration: Below 50% AI-generated

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.tenancy import bound_to_tenant
from app.models import OrgLink, SourceRecord


# This service retrieves the current records belonging to an organization.
# It joins records through their organization links, checks the tenant on both tables, and excludes historical versions so each logical record’s current membership is shown
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

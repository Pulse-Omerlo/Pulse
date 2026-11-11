# AI contribution: Below 50% AI-generated

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import OrgLink, SourceRecord


def current_records(
    session: Session,
    tenant_id: UUID,
    organization_id: UUID,
) -> list[SourceRecord]:
    """Current organization members: linked source_record versions with valid_to IS NULL."""
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

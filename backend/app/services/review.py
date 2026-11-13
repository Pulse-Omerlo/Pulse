# AI contribution: 50% or more AI-generated
"""The manual review queue: possible organization matches a person accepts or rejects.

Accepting links both records to one organization with `manual` links, creating it or
merging two organizations when needed. What it changed is stored on the candidate, by
record (source and external id) rather than by version, so undoing still works after a
sync gave the records new versions. Rejecting keeps the pair from being proposed again,
since matching skips rejected pairs. Matching never moves an organization with a manual
link, so an accepted pair stays together.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, select, update
from sqlalchemy.orm import Session, aliased

from app.core.errors import ConflictError, NotFoundError
from app.models import MatchCandidate, Organization, OrgLink, SourceRecord, Tenant
from app.services.browse import label
from app.services.pagination import Paging

PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"

Row = tuple[MatchCandidate, SourceRecord, SourceRecord]


def _current_organization_pair(left, right) -> ColumnElement[bool]:
    return and_(
        *(
            condition
            for record in (left, right)
            for condition in (record.kind == "organization", record.valid_to.is_(None))
        )
    )


def _with_records(statement, left, right):
    return statement.join(
        left,
        (left.tenant_id == MatchCandidate.tenant_id) & (left.id == MatchCandidate.left_record_id),
    ).join(
        right,
        (right.tenant_id == MatchCandidate.tenant_id)
        & (right.id == MatchCandidate.right_record_id),
    )


def review_queue(
    session: Session, tenant_id: uuid.UUID, paging: Paging, *, status: str = PENDING
) -> tuple[list[Row], int]:
    """Pairs of current organization versions with a status: pending by score, decided
    newest first. Pairs of older versions are history and are left out."""
    left, right = aliased(SourceRecord), aliased(SourceRecord)
    where = (
        MatchCandidate.tenant_id == tenant_id,
        MatchCandidate.status == status,
        _current_organization_pair(left, right),
    )
    order = (
        (MatchCandidate.score.desc(), MatchCandidate.id)
        if status == PENDING
        else (MatchCandidate.decided_at.desc().nulls_last(), MatchCandidate.id)
    )
    rows = session.execute(
        _with_records(select(MatchCandidate, left, right), left, right)
        .where(*where)
        .order_by(*order)
        .limit(paging.page_size)
        .offset(paging.offset)
    ).all()
    total = session.scalar(
        _with_records(select(func.count(MatchCandidate.id)), left, right).where(*where)
    )
    return [(c, lr, rr) for c, lr, rr in rows], total or 0


def _load(session: Session, tenant_id: uuid.UUID, candidate_id: uuid.UUID) -> Row:
    """The candidate with both records, locked; 404 unless it is a current organization pair."""
    left, right = aliased(SourceRecord), aliased(SourceRecord)
    row = session.execute(
        _with_records(select(MatchCandidate, left, right), left, right)
        .where(
            MatchCandidate.tenant_id == tenant_id,
            MatchCandidate.id == candidate_id,
            _current_organization_pair(left, right),
        )
        .with_for_update(of=MatchCandidate)
    ).first()
    if row is None:
        raise NotFoundError("Possible match not found; it may have changed since the page loaded")
    return row[0], row[1], row[2]


def _lock_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Serialize with matching, which takes the same lock."""
    session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


def _key(record: SourceRecord) -> dict[str, str]:
    return {"source_id": str(record.source_id), "external_id": record.external_id}


def _current_version(session: Session, tenant_id: uuid.UUID, key: dict[str, str]):
    return session.scalar(
        select(SourceRecord).where(
            SourceRecord.tenant_id == tenant_id,
            SourceRecord.source_id == uuid.UUID(key["source_id"]),
            SourceRecord.external_id == key["external_id"],
            SourceRecord.valid_to.is_(None),
        )
    )


def _link(session: Session, record: SourceRecord) -> OrgLink | None:
    return session.get(OrgLink, record.id)


def _resolved(session: Session, organization_id: uuid.UUID | None) -> Organization | None:
    """The organization, or the one it was merged into."""
    organization = session.get(Organization, organization_id) if organization_id else None
    while organization is not None and organization.merged_into is not None:
        organization = session.get(Organization, organization.merged_into)
    return organization


def _manual_link(record: SourceRecord, organization_id: uuid.UUID, decided_by: str) -> OrgLink:
    return OrgLink(
        tenant_id=record.tenant_id,
        source_record_id=record.id,
        organization_id=organization_id,
        method="manual",
        confidence=1.0,
        decided_by=decided_by,
        evidence=[{"decided_by": decided_by, "note": "accepted in the review queue"}],
    )


def _current_links(session: Session, organization_id: uuid.UUID) -> list[OrgLink]:
    return list(
        session.scalars(
            select(OrgLink)
            .join(
                SourceRecord,
                (SourceRecord.tenant_id == OrgLink.tenant_id)
                & (SourceRecord.id == OrgLink.source_record_id),
            )
            .where(OrgLink.organization_id == organization_id, SourceRecord.valid_to.is_(None))
        )
    )


def _decide(candidate: MatchCandidate, status: str, by: str, decision: dict | None) -> None:
    candidate.status = status
    candidate.decided_by = by
    candidate.decided_at = datetime.now(UTC)
    candidate.decision = decision


def accept(session: Session, tenant_id: uuid.UUID, candidate_id: uuid.UUID, decided_by: str) -> Row:
    """Put both records in one organization and record what changed."""
    _lock_tenant(session, tenant_id)
    candidate, left, right = _load(session, tenant_id, candidate_id)
    if candidate.status != PENDING:
        raise ConflictError(f"This pair was already {candidate.status}")
    links = {record.id: _link(session, record) for record in (left, right)}
    first, second = (
        _resolved(session, links[r.id].organization_id if links[r.id] else None)
        for r in (left, right)
    )
    decision: dict[str, Any] = {"linked": [], "created": None, "merged": None}

    if first is None and second is None:
        organization = Organization(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            display_name=label(left),
            primary_domain=left.domain or right.domain,
        )
        session.add(organization)
        session.flush()
        decision["created"] = str(organization.id)
        for record in (left, right):
            session.add(_manual_link(record, organization.id, decided_by))
            decision["linked"].append(_key(record))
    elif first is None or second is None:
        organization = first or second
        assert organization is not None
        loose = left if first is None else right
        session.add(_manual_link(loose, organization.id, decided_by))
        decision["linked"].append(_key(loose))
    elif first.id != second.id:
        decision["merged"] = _merge(session, tenant_id, first, second)

    _decide(candidate, ACCEPTED, decided_by, decision)
    session.flush()
    return candidate, left, right


def _merge(
    session: Session, tenant_id: uuid.UUID, first: Organization, second: Organization
) -> dict[str, Any]:
    """Merge the organization with fewer current records into the other one."""
    sizes = {o.id: len(_current_links(session, o.id)) for o in (first, second)}
    survivor, merged = sorted((first, second), key=lambda o: (-sizes[o.id], str(o.id)))
    moved = []
    for link in _current_links(session, merged.id):
        link.organization_id = survivor.id
        record = session.get(SourceRecord, link.source_record_id)
        moved.append(_key(record))
    earlier = list(
        session.scalars(select(Organization.id).where(Organization.merged_into == merged.id))
    )
    session.execute(
        update(Organization)
        .where(Organization.tenant_id == tenant_id, Organization.merged_into == merged.id)
        .values(merged_into=survivor.id)
        .execution_options(synchronize_session="fetch")
    )
    merged.merged_into = survivor.id
    return {
        "organization": str(merged.id),
        "into": str(survivor.id),
        "moved": moved,
        "earlier": [str(i) for i in earlier],
    }


def reject(session: Session, tenant_id: uuid.UUID, candidate_id: uuid.UUID, decided_by: str) -> Row:
    """Keep the pair apart; matching will not propose it again."""
    _lock_tenant(session, tenant_id)
    candidate, left, right = _load(session, tenant_id, candidate_id)
    if candidate.status != PENDING:
        raise ConflictError(f"This pair was already {candidate.status}")
    _decide(candidate, REJECTED, decided_by, None)
    session.flush()
    return candidate, left, right


def undo(session: Session, tenant_id: uuid.UUID, candidate_id: uuid.UUID) -> Row:
    """Reverse what accepting changed, or forget a rejection, and reopen the pair."""
    _lock_tenant(session, tenant_id)
    candidate, left, right = _load(session, tenant_id, candidate_id)
    if candidate.status == PENDING:
        raise ConflictError("This pair is not decided yet")
    if candidate.status == ACCEPTED and candidate.decision:
        _reverse(session, tenant_id, candidate.decision)
    candidate.status = PENDING
    candidate.decided_by = None
    candidate.decided_at = None
    candidate.decision = None
    session.flush()
    return candidate, left, right


def _reverse(session: Session, tenant_id: uuid.UUID, decision: dict[str, Any]) -> None:
    for key in decision.get("linked", []):
        record = _current_version(session, tenant_id, key)
        link = _link(session, record) if record else None
        if link is not None and link.method == "manual":
            session.delete(link)
    merged = decision.get("merged")
    if merged:
        organization_id, survivor_id = uuid.UUID(merged["organization"]), uuid.UUID(merged["into"])
        organization = session.get(Organization, organization_id)
        if organization is not None:
            organization.merged_into = None
        for key in merged["moved"]:
            record = _current_version(session, tenant_id, key)
            link = _link(session, record) if record else None
            if link is not None and link.organization_id == survivor_id:
                link.organization_id = organization_id
        earlier = [uuid.UUID(i) for i in merged["earlier"]]
        if earlier:
            session.execute(
                update(Organization)
                .where(Organization.tenant_id == tenant_id, Organization.id.in_(earlier))
                .values(merged_into=organization_id)
                .execution_options(synchronize_session="fetch")
            )
    session.flush()
    created = decision.get("created")
    if created:
        _drop_if_unused(session, tenant_id, uuid.UUID(created))


def _drop_if_unused(session: Session, tenant_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    """Delete an organization the decision created, unless anything still points at it."""
    used = session.scalar(
        select(func.count(OrgLink.source_record_id)).where(
            OrgLink.tenant_id == tenant_id, OrgLink.organization_id == organization_id
        )
    ) or session.scalar(
        select(func.count(Organization.id)).where(
            Organization.tenant_id == tenant_id, Organization.merged_into == organization_id
        )
    )
    if not used:
        session.execute(
            delete(Organization).where(
                Organization.tenant_id == tenant_id, Organization.id == organization_id
            )
        )


# --------------------------------------------------------------- values side by side


def _first(values: Any, field: str) -> str | None:
    for entry in values if isinstance(values, list) else []:
        if isinstance(entry, dict) and entry.get(field) and not entry.get("deleted_at"):
            return str(entry[field])
    return None


def record_values(record: SourceRecord) -> list[dict[str, Any]]:
    """Each compared field as stored in the source and as PULSE normalized it.

    Omerlo keeps contact details per locale; a file import keeps them as plain columns.
    """
    attributes = record.attributes or {}
    identifiers = attributes.get("identifiers") or {}
    contact, address = attributes.get("contact"), attributes.get("address")

    def raw(field: str, entries: Any = contact) -> str | None:
        return _first(entries, field) or (attributes.get(field) or None)

    def joined(key: str) -> str | None:
        values = identifiers.get(key) if isinstance(identifiers, dict) else None
        return ", ".join(values) if isinstance(values, list) and values else None

    return [
        {"field": "name", "value": label(record), "normalized": record.name_normalized},
        {"field": "website", "value": raw("website"), "normalized": record.domain},
        {"field": "linkedin", "value": raw("linkedin"), "normalized": record.linkedin},
        {"field": "email", "value": raw("email"), "normalized": joined("emails")},
        {"field": "phone", "value": raw("phone"), "normalized": joined("phones")},
        {"field": "city", "value": raw("city", address), "normalized": record.city},
    ]

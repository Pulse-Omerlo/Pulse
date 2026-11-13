# AI contribution: 50% or more AI-generated
"""Link the tenant's people to the organizations they work for, and read the links back."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models import Affiliation, OrgLink, Person, PersonLink, SourceRecord
from app.processing.affiliations import (
    LINKED,
    AffiliationDecision,
    OrganizationFacts,
    PersonFacts,
    affiliate,
    registered_domain,
)
from app.processing.entity_resolution import load_generic_domains
from app.processing.sector_mix import PersonUnit
from app.services.organization_views import current_organization, current_version, link_join
from app.services.pagination import Paging


@dataclass
class AffiliationReport:
    linked: int = 0
    conflicts: int = 0
    added: int = 0
    removed: int = 0

    def as_counts(self) -> dict[str, int]:
        return {
            "linked": self.linked,
            "conflicts": self.conflicts,
            "added": self.added,
            "removed": self.removed,
        }


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def organization_facts(session: Session, tenant_id: uuid.UUID) -> list[OrganizationFacts]:
    """Current organization versions with their domains, from their website and emails.

    Generic domains are kept here; the index of organizations leaves them out.
    """
    rows = session.execute(
        select(
            SourceRecord.id,
            SourceRecord.source_id,
            SourceRecord.external_id,
            SourceRecord.name_normalized,
            SourceRecord.domain,
            SourceRecord.attributes["identifiers"],
            OrgLink.organization_id,
        )
        .outerjoin(OrgLink, link_join())
        .where(current_organization(tenant_id))
    ).all()
    facts = []
    for record_id, source_id, external_id, name, domain, identifiers, organization_id in rows:
        identifiers = identifiers if isinstance(identifiers, dict) else {}
        domains = {domain, *strings(identifiers.get("domains"))}
        domains |= {registered_domain(email) for email in strings(identifiers.get("emails"))}
        facts.append(
            OrganizationFacts(
                record_id=record_id,
                key=organization_id or record_id,
                source_id=source_id,
                external_id=external_id,
                name_normalized=name,
                domains=frozenset(d for d in domains if d),
            )
        )
    return facts


def person_facts(session: Session, tenant_id: uuid.UUID) -> list[PersonFacts]:
    """Current person versions with only the fields the signals read."""
    attributes = SourceRecord.attributes
    rows = session.execute(
        select(
            SourceRecord.id,
            SourceRecord.source_id,
            attributes["identifiers"]["emails"],
            # Omerlo keeps one job title per locale; a file import has a single column.
            attributes["job_titles"],
            attributes["job_title"],
            attributes["organization_id"],
        ).where(current_version(tenant_id, "person"))
    ).all()
    return [
        PersonFacts(
            record_id=record_id,
            source_id=source_id,
            emails=tuple(strings(emails)),
            job_titles=tuple(dict.fromkeys([*strings(titles), *strings(title)])),
            organization_refs=tuple(ref.strip() for ref in strings(organization)),
        )
        for record_id, source_id, emails, titles, title, organization in rows
    ]


def link_people(session: Session, tenant_id: uuid.UUID) -> AffiliationReport:
    """Work out every current person's affiliations again and store what changed.

    Rows that still hold are kept with their decision time; rows of closed person versions
    are left as history. Runs inside the caller's transaction, bound to `tenant_id`.
    """
    excluded = load_generic_domains()
    decisions = affiliate(
        person_facts(session, tenant_id), organization_facts(session, tenant_id), excluded
    )
    wanted = {(d.person_record_id, d.organization_record_id): d for d in decisions}
    current_people = select(SourceRecord.id).where(current_version(tenant_id, "person"))
    existing = session.scalars(
        select(Affiliation).where(
            Affiliation.tenant_id == tenant_id,
            Affiliation.person_record_id.in_(current_people),
        )
    ).all()

    report = AffiliationReport()
    for row in existing:
        decision = wanted.pop((row.person_record_id, row.organization_record_id), None)
        if decision is None:
            session.delete(row)
            report.removed += 1
        elif not same(row, decision):
            row.status, row.method = decision.status, decision.method
            row.confidence, row.evidence = decision.confidence, list(decision.evidence)
            row.decided_at = func.now()
    for decision in wanted.values():
        session.add(as_row(tenant_id, decision))
        report.added += 1
    session.flush()

    for decision in decisions:
        if decision.status == LINKED:
            report.linked += 1
        else:
            report.conflicts += 1
    return report


def same(row: Affiliation, decision: AffiliationDecision) -> bool:
    return (row.status, row.method, row.confidence, row.evidence) == (
        decision.status,
        decision.method,
        decision.confidence,
        list(decision.evidence),
    )


def as_row(tenant_id: uuid.UUID, decision: AffiliationDecision) -> Affiliation:
    return Affiliation(
        tenant_id=tenant_id,
        person_record_id=decision.person_record_id,
        organization_record_id=decision.organization_record_id,
        status=decision.status,
        method=decision.method,
        confidence=decision.confidence,
        evidence=list(decision.evidence),
    )


def linked_people(session: Session, tenant_id: uuid.UUID) -> list[PersonUnit]:
    """Every current person once, with the organization of their strongest affiliation.

    The sector mix counts a person once, so a person with several affiliations is placed
    under the most confident one (ties go to the lowest organization key).
    """
    organization_record = aliased(SourceRecord)
    rows = session.execute(
        select(
            SourceRecord.id,
            SourceRecord.attributes["first_name"].astext,
            SourceRecord.attributes["last_name"].astext,
            SourceRecord.name_normalized,
            PersonLink.person_id,
            Person.display_name,
            Affiliation.confidence,
            organization_record.id,
            OrgLink.organization_id,
        )
        .outerjoin(
            PersonLink,
            (PersonLink.tenant_id == SourceRecord.tenant_id)
            & (PersonLink.source_record_id == SourceRecord.id),
        )
        .outerjoin(
            Person,
            (Person.tenant_id == PersonLink.tenant_id) & (Person.id == PersonLink.person_id),
        )
        .outerjoin(
            Affiliation,
            (Affiliation.tenant_id == SourceRecord.tenant_id)
            & (Affiliation.person_record_id == SourceRecord.id)
            & (Affiliation.status == LINKED),
        )
        .outerjoin(
            organization_record,
            (organization_record.tenant_id == Affiliation.tenant_id)
            & (organization_record.id == Affiliation.organization_record_id)
            & organization_record.valid_to.is_(None),
        )
        .outerjoin(
            OrgLink,
            (OrgLink.tenant_id == organization_record.tenant_id)
            & (OrgLink.source_record_id == organization_record.id),
        )
        .where(current_version(tenant_id, "person"))
    ).all()
    return people_units(rows)


def people_units(rows: Iterable[tuple]) -> list[PersonUnit]:
    names: dict[str, str] = {}
    best: dict[str, tuple[float, str]] = {}
    for record_id, first, last, normalized, person_id, display, confidence, org_record, org in rows:
        key = str(person_id or record_id)
        full_name = " ".join(part for part in (first, last) if part)
        names.setdefault(key, display or full_name or normalized or key)
        if org_record is None or confidence is None:
            continue
        candidate = (-confidence, str(org or org_record))
        if key not in best or candidate < best[key]:
            best[key] = candidate
    return [
        PersonUnit(id=key, name=names[key], organization_key=best[key][1] if key in best else None)
        for key in sorted(names)
    ]


def affiliations_page(
    session: Session, tenant_id: uuid.UUID, paging: Paging, *, status: str = LINKED
) -> tuple[list[tuple[Affiliation, SourceRecord, SourceRecord]], int]:
    """Current people's affiliations with a status, with both records, newest first."""
    person, organization = aliased(SourceRecord), aliased(SourceRecord)
    where = (
        Affiliation.tenant_id == tenant_id,
        Affiliation.status == status,
        person.valid_to.is_(None),
    )

    def joined(statement):
        return (
            statement.join(
                person,
                (person.tenant_id == Affiliation.tenant_id)
                & (person.id == Affiliation.person_record_id),
            )
            .join(
                organization,
                (organization.tenant_id == Affiliation.tenant_id)
                & (organization.id == Affiliation.organization_record_id),
            )
            .where(*where)
        )

    rows = session.execute(
        joined(select(Affiliation, person, organization))
        .order_by(Affiliation.decided_at.desc(), Affiliation.id)
        .limit(paging.page_size)
        .offset(paging.offset)
    ).all()
    total = session.scalar(joined(select(func.count(Affiliation.id))))
    return [(row, p, o) for row, p, o in rows], total or 0

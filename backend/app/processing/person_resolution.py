# AI contribution: 50% or more AI-generated
"""Tier 1 people matching: same name plus a secondary identifier."""

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from logging import getLogger
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import MatchCandidate, Person, PersonLink, SourceRecord, Tenant
from app.processing.entity_resolution import (
    RULE_VERSION,
    components,
    evidence,
    has_changed_record,
)

logger = getLogger(__name__)

LINKEDIN_PERSON_PREFIX = "in/"
MAX_PERSON_BLOCK = 64

PERSON_RULE_PRIORITIES = ("email", "phone", "linkedin", "organization_id")
WEAK_PERSON_RULES = frozenset({"name_only", "email_name_conflict", "linkedin_name_conflict"})
PERSON_RULE_WEIGHTS = {
    "email": 1.0,
    "phone": 1.0,
    "linkedin": 1.0,
    "organization_id": 1.0,
    "name_only": 0.5,
    "email_name_conflict": 0.5,
    "linkedin_name_conflict": 0.5,
}

Pair = tuple[UUID, UUID]
Fields = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PersonMatch:
    left: UUID
    right: UUID
    rule: str
    fields: Fields

    @property
    def providePair(self) -> Pair:
        return (self.left, self.right) if self.left < self.right else (self.right, self.left)

    @property
    def strong(self) -> bool:
        return self.rule not in WEAK_PERSON_RULES


@dataclass
class PersonMatchResult:
    people_created: int = 0
    links_created: int = 0
    candidates_created: int = 0
    people_merged: int = 0


def identifier_values(record: SourceRecord, key: str) -> set[str]:
    raw = (record.attributes or {}).get("identifiers")
    if not isinstance(raw, dict):
        return set()
    values = raw.get(key)
    if not isinstance(values, list):
        return set()
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


def person_linkedins(record: SourceRecord) -> set[str]:
    values = {
        value
        for value in identifier_values(record, "linkedin")
        if value.startswith(LINKEDIN_PERSON_PREFIX)
    }
    linkedin = record.linkedin
    if isinstance(linkedin, str) and linkedin.startswith(LINKEDIN_PERSON_PREFIX):
        values.add(linkedin)
    return values


def file_organization_id(record: SourceRecord) -> str | None:
    value = (record.attributes or {}).get("organization_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def person_blocking_keys(record: SourceRecord) -> Iterable[tuple[str, ...]]:
    for email in sorted(identifier_values(record, "emails")):
        yield "email", email
    for phone in sorted(identifier_values(record, "phones")):
        yield "phone", phone
    for linkedin in sorted(person_linkedins(record)):
        yield "linkedin", linkedin
    organization_id = file_organization_id(record)
    if organization_id is not None:
        yield "organization_id", str(record.source_id), organization_id
    if record.name_normalized:
        yield "name", record.name_normalized


def classify_person_pair(left: SourceRecord, right: SourceRecord) -> PersonMatch | None:
    if (
        left.id == right.id
        or left.tenant_id != right.tenant_id
        or left.kind != "person"
        or right.kind != "person"
        or left.valid_to is not None
        or right.valid_to is not None
    ):
        return None

    left, right = sorted((left, right), key=lambda record: record.id)
    same_name = bool(left.name_normalized and left.name_normalized == right.name_normalized)
    name_fields: Fields = (("name_normalized", left.name_normalized),) if same_name else ()

    shared_emails = identifier_values(left, "emails") & identifier_values(right, "emails")
    if shared_emails:
        email = min(shared_emails)
        fields: Fields = (
            (*name_fields, ("identifiers.emails", email))
            if same_name
            else (("identifiers.emails", email),)
        )
        return PersonMatch(
            left.id,
            right.id,
            "email" if same_name else "email_name_conflict",
            fields,
        )

    shared_phones = identifier_values(left, "phones") & identifier_values(right, "phones")
    if shared_phones and same_name:
        phone = min(shared_phones)
        return PersonMatch(
            left.id,
            right.id,
            "phone",
            (*name_fields, ("identifiers.phones", phone)),
        )

    shared_linkedin = person_linkedins(left) & person_linkedins(right)
    if shared_linkedin:
        linkedin = min(shared_linkedin)
        fields = (*name_fields, ("linkedin", linkedin)) if same_name else (("linkedin", linkedin),)
        return PersonMatch(
            left.id,
            right.id,
            "linkedin" if same_name else "linkedin_name_conflict",
            fields,
        )

    organization_id = file_organization_id(left)
    if (
        same_name
        and organization_id is not None
        and organization_id == file_organization_id(right)
        and left.source_id == right.source_id
    ):
        return PersonMatch(
            left.id,
            right.id,
            "organization_id",
            (*name_fields, ("organization_id", organization_id)),
        )

    if same_name:
        return PersonMatch(left.id, right.id, "name_only", name_fields)
    return None


def blocked_person_matches(records: Iterable[SourceRecord]) -> list[PersonMatch]:
    people = {
        record.id: record
        for record in records
        if record.kind == "person" and record.valid_to is None
    }
    blocks: dict[tuple, list[UUID]] = defaultdict(list)
    for record in people.values():
        for key in person_blocking_keys(record):
            blocks[(record.tenant_id, *key)].append(record.id)

    pairs: set[Pair] = set()
    for members in blocks.values():
        if len(members) > MAX_PERSON_BLOCK:
            logger.warning(
                "person_block_skipped",
                extra={"size": len(members), "cap": MAX_PERSON_BLOCK},
            )
            continue
        pairs.update(tuple(sorted(pair)) for pair in combinations(members, 2))

    matches = [
        match
        for left, right in sorted(pairs)
        if (match := classify_person_pair(people[left], people[right])) is not None
    ]
    order = (*PERSON_RULE_PRIORITIES, *sorted(WEAK_PERSON_RULES))
    return sorted(
        matches,
        key=lambda match: (order.index(match.rule), match.left, match.right),
    )


def conflicting_person_identifiers(records: Iterable[SourceRecord]) -> bool:
    linkedin = set()
    for record in records:
        linkedin |= person_linkedins(record)
    return len(linkedin) > 1


def match_people(
    session: Session, tenant_id: UUID, changed_ids: set[UUID] | None = None
) -> PersonMatchResult:
    with bound_to_tenant(session, tenant_id):
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None:
            raise ValueError(f"Unknown tenant {tenant_id}")

        session.flush()
        records = {
            record.id: record
            for record in session.scalars(
                select(SourceRecord)
                .where(
                    SourceRecord.tenant_id == tenant_id,
                    SourceRecord.kind == "person",
                    SourceRecord.valid_to.is_(None),
                )
                .order_by(SourceRecord.id)
            )
        }
        links = {
            link.source_record_id: link
            for link in session.scalars(
                select(PersonLink)
                .join(SourceRecord, SourceRecord.id == PersonLink.source_record_id)
                .where(
                    PersonLink.tenant_id == tenant_id,
                    SourceRecord.tenant_id == tenant_id,
                    SourceRecord.kind == "person",
                    SourceRecord.valid_to.is_(None),
                )
            )
        }
        decisions = {
            (candidate.left_record_id, candidate.right_record_id): candidate
            for candidate in session.scalars(
                select(MatchCandidate).where(MatchCandidate.tenant_id == tenant_id)
            )
            if candidate.left_record_id in records and candidate.right_record_id in records
        }

        matches = blocked_person_matches(records.values())
        people_created = links_created = candidates_created = 0
        merged_person_ids: set[UUID] = set()

        def add_candidate(match: PersonMatch) -> None:
            nonlocal candidates_created
            if match.providePair in decisions:
                return
            candidate = MatchCandidate(
                tenant_id=tenant_id,
                left_record_id=match.left,
                right_record_id=match.right,
                score=PERSON_RULE_WEIGHTS[match.rule],
                matched_fields=evidence(match, match.right),
                status="pending",
            )
            session.add(candidate)
            decisions[match.providePair] = candidate
            candidates_created += 1

        for group in components(matches):
            if not has_changed_record(group, changed_ids):
                continue
            edges = [
                match
                for match in matches
                if match.strong and match.left in group and match.right in group
            ]
            person_ids = {links[record_id].person_id for record_id in group if record_id in links}
            rejected = any(
                left in group and right in group and candidate.status == "rejected"
                for (left, right), candidate in decisions.items()
            )
            members = group | {
                record_id for record_id, link in links.items() if link.person_id in person_ids
            }
            unsafe = (
                rejected
                or len(person_ids) > 1
                or conflicting_person_identifiers(records[key] for key in members)
            )
            if unsafe:
                for match in edges:
                    add_candidate(match)
                continue

            if person_ids:
                person_id = next(iter(person_ids))
            else:
                first = records[min(group)]
                person_id = uuid4()
                display = (
                    " ".join(
                        part
                        for part in (
                            (first.attributes or {}).get("first_name"),
                            (first.attributes or {}).get("last_name"),
                        )
                        if isinstance(part, str) and part.strip()
                    )
                    or first.name_normalized
                    or first.external_id
                )
                session.add(Person(id=person_id, tenant_id=tenant_id, display_name=display))
                session.flush()
                people_created += 1

            adjacency: dict[UUID, list[tuple[UUID, PersonMatch]]] = defaultdict(list)
            for match in edges:
                adjacency[match.left].append((match.right, match))
                adjacency[match.right].append((match.left, match))

            roots = sorted(group & links.keys())
            queue = deque(roots or [min(group)])
            visited = set(queue)

            while queue:
                record_id = queue.popleft()
                for neighbor, match in adjacency[record_id]:
                    if neighbor in visited:
                        continue
                    for endpoint, other in ((record_id, neighbor), (neighbor, record_id)):
                        if endpoint in links:
                            continue
                        link = PersonLink(
                            tenant_id=tenant_id,
                            source_record_id=endpoint,
                            person_id=person_id,
                            method=match.rule,
                            confidence=PERSON_RULE_WEIGHTS[match.rule],
                            decided_by=f"tier1:{RULE_VERSION}",
                            evidence=evidence(match, other),
                        )
                        session.add(link)
                        links[endpoint] = link
                        links_created += 1
                        merged_person_ids.add(person_id)
                    visited.add(neighbor)
                    queue.append(neighbor)

        for match in matches:
            if match.strong or not has_changed_record([match.left, match.right], changed_ids):
                continue
            left_link, right_link = links.get(match.left), links.get(match.right)
            if left_link and right_link and left_link.person_id == right_link.person_id:
                continue
            add_candidate(match)

        session.flush()
        return PersonMatchResult(
            people_created=people_created,
            links_created=links_created,
            candidates_created=candidates_created,
            people_merged=len(merged_person_ids),
        )

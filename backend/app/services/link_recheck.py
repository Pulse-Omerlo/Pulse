# AI contribution: 50% or more AI-generated
"""Drop automatic org and person links whose evidence no longer holds on a new version."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import batched, chain
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import OrgLink, PersonLink, SourceRecord, SyncRun
from app.processing.entity_resolution import load_generic_domains
from app.processing.link_recheck import (
    ORGANIZATION_RECHECK,
    PERSON_RECHECK,
    REASON_UNVERIFIABLE,
    RecheckKind,
    evidence_holds,
    matching_changes,
)
from app.services.source_records import BATCH_SIZE

logger = logging.getLogger(__name__)

LINK_RECHECK_KEY = "link_recheck"

LinkRow = OrgLink | PersonLink


@dataclass(frozen=True)
class RecheckResult:
    """Counters for one recheck pass inside store()."""

    checked: int = 0
    kept: int = 0
    dropped: int = 0
    unverifiable: int = 0
    dropped_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class LinkKind:
    """ORM wiring for one RecheckKind."""

    recheck: RecheckKind
    model: type[LinkRow]
    entity_id_of: Callable[[LinkRow], UUID]
    entity_column: object


LINK_KINDS = (
    LinkKind(
        ORGANIZATION_RECHECK,
        OrgLink,
        lambda link: link.organization_id,
        OrgLink.organization_id,
    ),
    LinkKind(
        PERSON_RECHECK,
        PersonLink,
        lambda link: link.person_id,
        PersonLink.person_id,
    ),
)


@dataclass
class _Counts:
    checked: int = 0
    kept: int = 0
    dropped: int = 0
    unverifiable: int = 0
    dropped_ids: list[UUID] = field(default_factory=list)

    def freeze(self) -> RecheckResult:
        return RecheckResult(
            checked=self.checked,
            kept=self.kept,
            dropped=self.dropped,
            unverifiable=self.unverifiable,
            dropped_ids=tuple(self.dropped_ids),
        )


def recheck_links(
    session: Session,
    run: SyncRun,
    carried: Sequence[tuple[UUID, UUID]],
    *,
    excluded_domains: frozenset[str] | None = None,
) -> RecheckResult:
    """Delete automatic links on new versions whose matching evidence no longer holds.

    Rematch is left to match_after_sync. Old versions are never modified. Org and person
    counts are summed under the same link_recheck key.
    """
    if not carried:
        return RecheckResult()

    with bound_to_tenant(session, run.tenant_id):
        versions = _load_versions(session, carried)
        excluded = excluded_domains if excluded_domains is not None else load_generic_domains()
        counts = _Counts()
        for kind in LINK_KINDS:
            _recheck_kind(session, run, carried, versions, kind, excluded, counts)
        if counts.dropped:
            session.flush()
        result = counts.freeze()
        logger.info(
            "automatic links rechecked",
            extra={
                "sync_run_id": str(run.id),
                "checked": result.checked,
                "kept": result.kept,
                "dropped": result.dropped,
                "unverifiable": result.unverifiable,
            },
        )
        return result


def _recheck_kind(
    session: Session,
    run: SyncRun,
    carried: Sequence[tuple[UUID, UUID]],
    versions: dict[UUID, SourceRecord],
    kind: LinkKind,
    excluded: frozenset[str],
    counts: _Counts,
) -> None:
    spec = kind.recheck
    changed = _changed_new_ids(carried, versions, spec)
    if not changed:
        return

    links = _automatic_links(session, run.tenant_id, changed, kind)
    if not links:
        return

    entity_ids = {kind.entity_id_of(link) for link in links}
    members_by_entity = _current_members(session, run.tenant_id, entity_ids, kind)
    for link in links:
        record = versions[link.source_record_id]
        members = members_by_entity.get(kind.entity_id_of(link), ())
        verdict = evidence_holds(
            link.method,
            link.evidence,
            record,
            members,
            excluded,
            automatic_methods=spec.automatic_methods,
            known_fields=spec.known_fields,
            record_has=spec.record_has,
        )
        counts.checked += 1
        if verdict.reason == REASON_UNVERIFIABLE:
            counts.unverifiable += 1
            logger.warning(
                "automatic link evidence unverifiable",
                extra={
                    "sync_run_id": str(run.id),
                    "source_record_id": str(link.source_record_id),
                    "kind": spec.kind,
                    "entity_id": str(kind.entity_id_of(link)),
                    "reason": verdict.reason,
                    "method": link.method,
                },
            )
        if verdict.holds:
            counts.kept += 1
            continue
        session.delete(link)
        counts.dropped += 1
        counts.dropped_ids.append(link.source_record_id)


def _load_versions(
    session: Session, carried: Sequence[tuple[UUID, UUID]]
) -> dict[UUID, SourceRecord]:
    ids = set(chain.from_iterable(carried))
    found: dict[UUID, SourceRecord] = {}
    for batch in batched(ids, BATCH_SIZE):
        found.update(
            {
                record.id: record
                for record in session.scalars(
                    select(SourceRecord).where(SourceRecord.id.in_(batch))
                )
            }
        )
    return found


def _changed_new_ids(
    carried: Sequence[tuple[UUID, UUID]],
    versions: dict[UUID, SourceRecord],
    spec: RecheckKind,
) -> set[UUID]:
    changed: set[UUID] = set()
    for old_id, new_id in carried:
        before = versions.get(old_id)
        after = versions.get(new_id)
        if before is None or after is None or after.kind != spec.kind:
            continue
        if matching_changes(before, after, fields=spec.matching_fields, value_of=spec.value_of):
            changed.add(new_id)
    return changed


def _automatic_links(
    session: Session, tenant_id: UUID, source_record_ids: set[UUID], kind: LinkKind
) -> list[LinkRow]:
    model = kind.model
    links: list[LinkRow] = []
    for batch in batched(source_record_ids, BATCH_SIZE):
        links.extend(
            session.scalars(
                select(model).where(
                    model.tenant_id == tenant_id,
                    model.source_record_id.in_(batch),
                    model.method.in_(kind.recheck.automatic_methods),
                )
            )
        )
    return links


def _current_members(
    session: Session,
    tenant_id: UUID,
    entity_ids: Iterable[UUID],
    kind: LinkKind,
) -> dict[UUID, tuple[SourceRecord, ...]]:
    ids = set(entity_ids)
    if not ids:
        return {}
    by_entity: dict[UUID, list[SourceRecord]] = defaultdict(list)
    model = kind.model
    id_column = kind.entity_column
    for batch in batched(ids, BATCH_SIZE):
        rows = session.execute(
            select(id_column, SourceRecord)
            .join(SourceRecord, SourceRecord.id == model.source_record_id)
            .where(
                model.tenant_id == tenant_id,
                SourceRecord.tenant_id == tenant_id,
                id_column.in_(batch),
                SourceRecord.kind == kind.recheck.kind,
                SourceRecord.valid_to.is_(None),
            )
        )
        for entity_id, record in rows:
            by_entity[entity_id].append(record)
    return {entity_id: tuple(members) for entity_id, members in by_entity.items()}

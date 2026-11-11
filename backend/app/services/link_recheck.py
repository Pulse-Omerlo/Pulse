# AI contribution: 50% or more AI-generated
"""Drop automatic org links whose evidence no longer holds on a new version."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import batched
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import OrgLink, SourceRecord, SyncRun
from app.models.resolution import AUTOMATIC_LINK_METHODS
from app.processing.entity_resolution import load_generic_domains
from app.processing.link_recheck import (
    REASON_UNVERIFIABLE,
    evidence_holds,
    matching_changes,
)
from app.services.source_records import BATCH_SIZE

logger = logging.getLogger(__name__)

LINK_RECHECK_KEY = "link_recheck"


@dataclass(frozen=True)
class RecheckResult:
    """Counters for one recheck pass inside store()."""

    checked: int = 0
    kept: int = 0
    dropped: int = 0
    unverifiable: int = 0
    dropped_ids: tuple[UUID, ...] = ()


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

    Rematch is left to match_after_sync. Old versions are never modified.
    """
    if not carried:
        return RecheckResult()

    with bound_to_tenant(session, run.tenant_id):
        versions = _load_versions(session, carried)
        changed = _changed_new_ids(carried, versions)
        if not changed:
            return RecheckResult()

        links = _automatic_links(session, run.tenant_id, changed)
        if not links:
            return RecheckResult()

        members_by_org = _current_members(
            session, run.tenant_id, {link.organization_id for link in links}
        )
        excluded = excluded_domains if excluded_domains is not None else load_generic_domains()
        counts = _Counts()
        for link in links:
            record = versions[link.source_record_id]
            members = members_by_org.get(link.organization_id, ())
            verdict = evidence_holds(link.method, link.evidence, record, members, excluded)
            counts.checked += 1
            if verdict.reason == REASON_UNVERIFIABLE:
                counts.unverifiable += 1
                logger.warning(
                    "automatic link evidence unverifiable",
                    extra={
                        "sync_run_id": str(run.id),
                        "source_record_id": str(link.source_record_id),
                        "organization_id": str(link.organization_id),
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


def _load_versions(
    session: Session, carried: Sequence[tuple[UUID, UUID]]
) -> dict[UUID, SourceRecord]:
    ids = {record_id for pair in carried for record_id in pair}
    found: dict[UUID, SourceRecord] = {}
    for batch in batched(ids, BATCH_SIZE):
        found.update(
            (record.id, record)
            for record in session.scalars(
                select(SourceRecord).where(
                    SourceRecord.id.in_(batch),
                    SourceRecord.kind == "organization",
                )
            )
        )
    return found


def _changed_new_ids(
    carried: Sequence[tuple[UUID, UUID]],
    versions: dict[UUID, SourceRecord],
) -> set[UUID]:
    changed: set[UUID] = set()
    for old_id, new_id in carried:
        before = versions.get(old_id)
        after = versions.get(new_id)
        if before is None or after is None:
            continue
        if after.kind != "organization":
            continue
        if matching_changes(before, after):
            changed.add(new_id)
    return changed


def _automatic_links(
    session: Session, tenant_id: UUID, source_record_ids: set[UUID]
) -> list[OrgLink]:
    links: list[OrgLink] = []
    for batch in batched(source_record_ids, BATCH_SIZE):
        links.extend(
            session.scalars(
                select(OrgLink).where(
                    OrgLink.tenant_id == tenant_id,
                    OrgLink.source_record_id.in_(batch),
                    OrgLink.method.in_(AUTOMATIC_LINK_METHODS),
                )
            )
        )
    return links


def _current_members(
    session: Session, tenant_id: UUID, organization_ids: Iterable[UUID]
) -> dict[UUID, tuple[SourceRecord, ...]]:
    org_ids = set(organization_ids)
    if not org_ids:
        return {}
    by_org: dict[UUID, list[SourceRecord]] = defaultdict(list)
    for batch in batched(org_ids, BATCH_SIZE):
        rows = session.execute(
            select(OrgLink.organization_id, SourceRecord)
            .join(SourceRecord, SourceRecord.id == OrgLink.source_record_id)
            .where(
                OrgLink.tenant_id == tenant_id,
                SourceRecord.tenant_id == tenant_id,
                OrgLink.organization_id.in_(batch),
                SourceRecord.kind == "organization",
                SourceRecord.valid_to.is_(None),
            )
        )
        for organization_id, record in rows:
            by_org[organization_id].append(record)
    return {organization_id: tuple(members) for organization_id, members in by_org.items()}

# AI contribution: 50% or more AI-generated
"""Compute and store a network's sector mix after a sync run, and read it back."""

import os
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    IndustryClassification,
    Organization,
    OrgLink,
    SectorMixResult,
    SourceRecord,
    SyncRun,
    Tenant,
)
from app.processing.sector_mix import (
    MixSettings,
    OrganizationUnit,
    PersonUnit,
    SectorMix,
    compute_mix,
    load_settings,
)

# One optional <omerlo_media_id or tenant id>.toml per network; see the README in this folder.
DEFAULT_SETTINGS_DIR = Path(__file__).parents[1] / "processing" / "sector_targets"

# The people of a tenant with the organization each works for.
PeopleSource = Callable[[Session, uuid.UUID], list[PersonUnit]]


def settings_dir() -> Path:
    return Path(os.environ.get("SECTOR_TARGETS_DIR", DEFAULT_SETTINGS_DIR))


def settings_for(tenant: Tenant) -> MixSettings:
    return load_settings(settings_dir() / f"{tenant.omerlo_media_id or tenant.id}.toml")


def organization_units(session: Session, tenant_id: uuid.UUID) -> list[OrganizationUnit]:
    """The tenant's organizations: current versions grouped by the organization they match.

    A version linked to no organization counts as an organization of its own. One query,
    reading only the columns needed, so a large network is not loaded record by record.
    """
    rows = session.execute(
        select(
            SourceRecord.id,
            SourceRecord.attributes["name"].astext,
            SourceRecord.name_normalized,
            SourceRecord.external_id,
            OrgLink.organization_id,
            Organization.display_name,
            IndustryClassification.primary_sector,
        )
        .outerjoin(
            OrgLink,
            (OrgLink.tenant_id == SourceRecord.tenant_id)
            & (OrgLink.source_record_id == SourceRecord.id),
        )
        .outerjoin(
            Organization,
            (Organization.tenant_id == OrgLink.tenant_id)
            & (Organization.id == OrgLink.organization_id),
        )
        .outerjoin(
            IndustryClassification,
            (IndustryClassification.tenant_id == SourceRecord.tenant_id)
            & (IndustryClassification.source_record_id == SourceRecord.id),
        )
        .where(
            SourceRecord.tenant_id == tenant_id,
            SourceRecord.kind == "organization",
            SourceRecord.valid_to.is_(None),
        )
    ).all()

    names: dict[str, str] = {}
    sectors: dict[str, dict[str, str | None]] = {}
    for record_id, name, normalized, external_id, organization_id, display, sector in rows:
        key = str(organization_id or record_id)
        names.setdefault(key, display or name or normalized or external_id)
        sectors.setdefault(key, {})[str(record_id)] = sector
    return [
        OrganizationUnit(key=key, name=names[key], record_sectors=sectors[key]) for key in names
    ]


def measure_sector_mix(
    session: Session, run: SyncRun, people: PeopleSource | None = None
) -> SectorMixResult:
    """Compute the tenant's sector mix and store it for `run`, replacing an earlier result.

    Without `people`, people are not linked to organizations yet and their shares stay empty.

    Runs inside the caller's transaction, in a session bound to the run's tenant.
    """
    tenant = session.get_one(Tenant, run.tenant_id)
    settings = settings_for(tenant)
    linked = people(session, run.tenant_id) if people else None
    mix = compute_mix(organization_units(session, run.tenant_id), linked, settings)
    session.execute(
        delete(SectorMixResult).where(
            SectorMixResult.tenant_id == run.tenant_id, SectorMixResult.sync_run_id == run.id
        )
    )
    result = SectorMixResult(tenant_id=run.tenant_id, sync_run_id=run.id, **as_row(mix, settings))
    session.add(result)
    session.flush()
    return result


def as_row(mix: SectorMix, settings: MixSettings) -> dict:
    return {
        "settings": {
            "gap_points": settings.gap_points,
            "targets": dict(settings.targets),
            "version": settings.version,
        },
        "organizations": mix.organizations,
        "classified": mix.classified,
        "people_status": mix.people_status,
        "people": mix.people,
        "sectors": [
            {
                "code": share.code,
                "name": share.name,
                "organization_count": len(share.organizations),
                "organization_share": round(share.organization_share, 1),
                "people_count": None if share.people is None else len(share.people),
                "people_share": (
                    None if share.people_share is None else round(share.people_share, 1)
                ),
                "target": share.target,
                "organizations": [organization_json(unit) for unit in share.organizations],
                "people": (
                    None if share.people is None else [person_json(p) for p in share.people]
                ),
            }
            for share in mix.sectors
        ],
        "gaps": [
            {"code": gap.code, "kind": gap.kind, "size": gap.size, "explanation": gap.explanation}
            for gap in mix.gaps
        ],
        "unclassified": [organization_json(unit) for unit in mix.unclassified],
        "people_unplaced": [person_json(p) for p in mix.people_unplaced],
    }


def organization_json(unit: OrganizationUnit) -> dict:
    return {"key": unit.key, "name": unit.name, "records": sorted(unit.record_sectors)}


def person_json(person: PersonUnit) -> dict:
    return {"id": person.id, "name": person.name, "organization": person.organization_key}


def latest_mix(session: Session, tenant_id: uuid.UUID) -> SectorMixResult | None:
    """The result of the tenant's most recent run that has one."""
    return session.scalar(
        select(SectorMixResult)
        .join(
            SyncRun,
            (SyncRun.tenant_id == SectorMixResult.tenant_id)
            & (SyncRun.id == SectorMixResult.sync_run_id),
        )
        .where(SectorMixResult.tenant_id == tenant_id)
        .order_by(SyncRun.started_at.desc(), SectorMixResult.computed_at.desc())
        .limit(1)
    )


def mix_for_run(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> SectorMixResult | None:
    return session.scalar(
        select(SectorMixResult).where(
            SectorMixResult.tenant_id == tenant_id, SectorMixResult.sync_run_id == run_id
        )
    )

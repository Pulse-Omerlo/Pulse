# AI contribution: 50% or more AI-generated
"""Recheck automatic org links against PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import Organization, OrgLink, Source, SourceRecord, SyncRun, Tenant
from app.processing.entity_resolution import match_organizations
from app.services.link_recheck import RecheckResult, recheck_links
from app.services.organizations import current_records, members_as_of
from app.services.source_records import upsert_records

pytestmark = pytest.mark.integration
START = datetime(2026, 11, 1, tzinfo=UTC)
NEXT = START + timedelta(days=1)


def setup_records(session, fields):
    tenant = Tenant(name="Link recheck test")
    session.add(tenant)
    session.flush()
    source = Source(tenant_id=tenant.id, kind="mock")
    session.add(source)
    session.flush()
    run = SyncRun(tenant_id=tenant.id, source_id=source.id, started_at=START)
    session.add(run)
    session.flush()
    mapped = [
        MappedRecord(
            **(
                {
                    "kind": "organization",
                    "external_id": str(n),
                    "workspace_id": None,
                    "name_normalized": f"org {n}",
                }
                | data
            )
        )
        for n, data in enumerate(fields)
    ]
    upsert_records(session, run, mapped)
    records = list(
        session.scalars(
            select(SourceRecord)
            .where(SourceRecord.source_id == source.id)
            .order_by(SourceRecord.external_id)
        )
    )
    return tenant, run, mapped, records


def next_run(session, run: SyncRun) -> SyncRun:
    follow = SyncRun(tenant_id=run.tenant_id, source_id=run.source_id, started_at=NEXT)
    session.add(follow)
    session.flush()
    return follow


def current_by_external(session, tenant_id: UUID) -> dict[str, SourceRecord]:
    return {
        record.external_id: record
        for record in session.scalars(
            select(SourceRecord).where(
                SourceRecord.tenant_id == tenant_id,
                SourceRecord.valid_to.is_(None),
            )
        )
    }


def link_snapshot(link: OrgLink) -> tuple:
    return (
        link.organization_id,
        link.method,
        link.confidence,
        link.decided_by,
        link.decided_at,
        link.evidence,
    )


def test_changed_domain_drops_new_link_and_keeps_old(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    old_a, old_b = records
    org_id = session.get(OrgLink, old_a.id).organization_id
    old_link = link_snapshot(session.get(OrgLink, old_a.id))
    follow = next_run(session, run)

    result = upsert_records(session, follow, [replace(mapped[0], domain="other.ca"), mapped[1]])
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()
    current = current_by_external(session, tenant.id)

    assert rechecked == RecheckResult(
        checked=1, kept=0, dropped=1, unverifiable=0, dropped_ids=(current["0"].id,)
    )
    assert session.get(OrgLink, current["0"].id) is None
    assert link_snapshot(session.get(OrgLink, old_a.id)) == old_link
    assert session.get(OrgLink, current["1"].id).organization_id == org_id
    assert session.get(OrgLink, old_b.id) is not None


def test_attribute_only_change_does_not_recheck(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    before = link_snapshot(session.get(OrgLink, records[0].id))
    follow = next_run(session, run)

    result = upsert_records(
        session, follow, [replace(mapped[0], attributes={"note": "x"}), mapped[1]]
    )
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()
    current = current_by_external(session, tenant.id)

    assert rechecked == RecheckResult()
    assert link_snapshot(session.get(OrgLink, current["0"].id)) == before


def test_rename_on_domain_link_is_kept(session):
    tenant, run, mapped, _records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    follow = next_run(session, run)

    result = upsert_records(
        session,
        follow,
        [replace(mapped[0], name_normalized="renamed exemple"), mapped[1]],
    )
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()
    current = current_by_external(session, tenant.id)

    assert rechecked.checked == 1 and rechecked.kept == 1 and rechecked.dropped == 0
    assert session.get(OrgLink, current["0"].id) is not None


def test_manual_link_survives_domain_change(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "other.ca"}]
    )
    organization = Organization(tenant_id=tenant.id, display_name="Manual org")
    session.add(organization)
    session.flush()
    session.add(
        OrgLink(
            tenant_id=tenant.id,
            source_record_id=records[0].id,
            organization_id=organization.id,
            method="manual",
            confidence=1,
            decided_by="reviewer",
            evidence=[{"decided_by": "reviewer"}],
        )
    )
    session.flush()
    follow = next_run(session, run)

    result = upsert_records(session, follow, [replace(mapped[0], domain="changed.ca"), mapped[1]])
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()
    current = current_by_external(session, tenant.id)

    assert rechecked == RecheckResult()
    copied = session.get(OrgLink, current["0"].id)
    assert copied.method == "manual" and copied.organization_id == organization.id


def test_empty_evidence_automatic_link_is_unverifiable_keep(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "other.ca"}]
    )
    organization = Organization(tenant_id=tenant.id, display_name="Legacy org")
    session.add(organization)
    session.flush()
    session.add(
        OrgLink(
            tenant_id=tenant.id,
            source_record_id=records[0].id,
            organization_id=organization.id,
            method="domain",
            confidence=1,
            decided_by="tier1",
            evidence=[],
        )
    )
    session.flush()
    follow = next_run(session, run)

    result = upsert_records(session, follow, [replace(mapped[0], domain="changed.ca"), mapped[1]])
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()
    current = current_by_external(session, tenant.id)

    assert rechecked == RecheckResult(checked=1, kept=1, dropped=0, unverifiable=1)
    assert session.get(OrgLink, current["0"].id) is not None


def test_members_as_of_keeps_history_after_drop(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    org_id = session.get(OrgLink, records[0].id).organization_id
    before = {record.id for record in members_as_of(session, tenant.id, org_id, START)}
    assert before == {records[0].id, records[1].id}

    follow = next_run(session, run)
    result = upsert_records(session, follow, [replace(mapped[0], domain="other.ca"), mapped[1]])
    recheck_links(session, follow, result.carried)
    session.expire_all()

    assert {record.id for record in members_as_of(session, tenant.id, org_id, START)} == before
    after = members_as_of(session, tenant.id, org_id, NEXT)
    assert {record.external_id for record in after} == {"1"}
    assert current_records(session, tenant.id, org_id)[0].external_id == "1"


def test_empty_organization_contract_when_both_members_leave(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    org_id = session.get(OrgLink, records[0].id).organization_id
    follow = next_run(session, run)

    result = upsert_records(
        session,
        follow,
        [replace(mapped[0], domain="a.ca"), replace(mapped[1], domain="b.ca")],
    )
    rechecked = recheck_links(session, follow, result.carried)
    session.expire_all()

    assert rechecked.dropped == 2
    assert session.get(Organization, org_id) is not None
    assert current_records(session, tenant.id, org_id) == []
    # Contract for a future list endpoint: only organizations with current members.
    listed = [
        org.id
        for org in session.scalars(select(Organization).where(Organization.tenant_id == tenant.id))
        if current_records(session, tenant.id, org.id)
    ]
    assert org_id not in listed
    assert {record.id for record in members_as_of(session, tenant.id, org_id, START)} == {
        records[0].id,
        records[1].id,
    }


def test_members_as_of_unknown_org_and_other_tenant_are_empty(session):
    tenant, _, _, records = setup_records(session, [{"domain": "exemple.ca"}] * 2)
    match_organizations(session, tenant.id)
    org_id = session.get(OrgLink, records[0].id).organization_id
    other = Tenant(name="Other tenant")
    session.add(other)
    session.flush()

    assert members_as_of(session, tenant.id, UUID(int=999), START) == []
    assert members_as_of(session, other.id, org_id, START) == []


def test_old_link_row_is_byte_for_byte_unchanged(session):
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    old = session.get(OrgLink, records[0].id)
    snapshot = link_snapshot(old)
    follow = next_run(session, run)

    result = upsert_records(session, follow, [replace(mapped[0], domain="other.ca"), mapped[1]])
    recheck_links(session, follow, result.carried)
    session.expire_all()

    assert link_snapshot(session.get(OrgLink, records[0].id)) == snapshot

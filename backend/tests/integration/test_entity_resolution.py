# AI contribution: 50% or more AI-generated
"""Matching decisions and version carry-forward against PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import MatchCandidate, Organization, OrgLink, Source, SourceRecord, SyncRun, Tenant
from app.processing.entity_resolution import MatchResult, match_organizations
from app.services.organizations import current_records
from app.services.source_records import upsert_records

pytestmark = pytest.mark.integration
START = datetime(2026, 11, 1, tzinfo=UTC)


def setup_records(session, fields):
    tenant = Tenant(name="Matching test")
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


def manual_link(session, record):
    organization = Organization(tenant_id=record.tenant_id, display_name="Verified company")
    session.add(organization)
    session.flush()
    link = OrgLink(
        tenant_id=record.tenant_id,
        source_record_id=record.id,
        organization_id=organization.id,
        method="manual",
        confidence=1,
        decided_by="reviewer",
        evidence=[{"decided_by": "reviewer"}],
    )
    session.add(link)
    session.flush()
    return link


def candidate(session, records, status):
    left, right = sorted(record.id for record in records)
    row = MatchCandidate(
        tenant_id=records[0].tenant_id,
        left_record_id=left,
        right_record_id=right,
        score=0.5,
        status=status,
        matched_fields=[],
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize(
    "fields,method",
    [
        ({"domain": "exemple.ca"}, "domain"),
        ({"linkedin": "company/exemple"}, "linkedin"),
        ({"external_ids": {"neq": ["123"]}}, "external_id"),
        ({"name_normalized": "exemple", "city": "quebec"}, "name_city"),
    ],
)
def test_rules_save_links_and_rerunning_creates_nothing(session, fields, method):
    # Arrange
    tenant, _, _, records = setup_records(session, [fields, fields])
    # Act
    first = match_organizations(session, tenant.id)
    session.expire_all()
    second = match_organizations(session, tenant.id)
    links = [session.get(OrgLink, record.id) for record in records]
    # Assert
    assert first == MatchResult(1, 2, 0) and second == MatchResult()
    assert links[0].organization_id == links[1].organization_id
    for record, link in zip(records, links, strict=True):
        assert link.method == method and 0 < link.confidence <= 1
        assert link.evidence[0]["rule_set_version"] == "tier1-v1"
        assert link.evidence[0]["matched_record_id"] in {
            str(other.id) for other in records if other.id != record.id
        }


def test_manual_link_is_reused_without_overwriting_its_evidence(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [{"domain": "exemple.ca"}] * 2)
    original = manual_link(session, records[0])
    snapshot = (original.organization_id, original.evidence, original.decided_at)
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result == MatchResult(0, 1, 0)
    assert (original.organization_id, original.evidence, original.decided_at) == snapshot
    assert original.method == "manual" and original.decided_by == "reviewer"
    assert session.get(OrgLink, records[1].id).organization_id == original.organization_id


def test_existing_organizations_are_not_merged(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [{"domain": "exemple.ca"}] * 2)
    links = [manual_link(session, record) for record in records]
    ids = [link.organization_id for link in links]
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult(0, 0, 1)
    assert [link.organization_id for link in links] == ids
    assert match_organizations(session, tenant.id) == MatchResult()


def test_rejection_cannot_be_bypassed_through_a_third_record(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca", "linkedin": "company/exemple"},
            {"linkedin": "company/exemple"},
        ],
    )
    decision = candidate(session, [records[0], records[2]], "rejected")
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result == MatchResult(0, 0, 2)
    assert decision.status == "rejected"
    assert all(session.get(OrgLink, record.id) is None for record in records)
    assert match_organizations(session, tenant.id) == MatchResult()


@pytest.mark.parametrize("status", ["pending", "accepted", "rejected"])
def test_new_versions_keep_manual_links_and_candidate_decisions(session, status):
    # Arrange
    tenant, run, mapped, records = setup_records(
        session,
        [
            {"name_normalized": "exemple", "city": "quebec"},
            {"name_normalized": "exemple", "city": "montreal"},
        ],
    )
    original = manual_link(session, records[0])
    candidate(session, records, status)
    next_run = SyncRun(
        tenant_id=tenant.id, source_id=run.source_id, started_at=START + timedelta(days=1)
    )
    session.add(next_run)
    session.flush()
    # Act
    upsert_records(
        session, next_run, [replace(item, attributes={"note": "updated"}) for item in mapped]
    )
    result = match_organizations(session, tenant.id)
    session.expire_all()
    current = list(
        session.scalars(
            select(SourceRecord).where(
                SourceRecord.tenant_id == tenant.id, SourceRecord.valid_to.is_(None)
            )
        )
    )
    ids = {record.id for record in current}
    decisions = list(
        session.scalars(
            select(MatchCandidate).where(
                MatchCandidate.left_record_id.in_(ids), MatchCandidate.right_record_id.in_(ids)
            )
        )
    )
    members = current_records(session, tenant.id, original.organization_id)
    # Assert
    assert result == MatchResult() and len(decisions) == 1
    assert decisions[0].status == status
    assert len(members) == 1 and members[0].id in ids
    copied = session.get(OrgLink, members[0].id)
    assert copied.method == "manual" and copied.evidence == original.evidence
    assert all(record.id not in ids for record in records)


def test_matching_and_membership_do_not_cross_tenants(session):
    # Arrange
    first, _, _, records = setup_records(session, [{"domain": "exemple.ca"}] * 2)
    second, _, _, others = setup_records(session, [{"domain": "exemple.ca"}])
    # Act
    match_organizations(session, first.id)
    result = match_organizations(session, second.id)
    org_id = session.get(OrgLink, records[0].id).organization_id
    # Assert
    assert result == MatchResult() and session.get(OrgLink, others[0].id) is None
    assert current_records(session, second.id, org_id) == []
    assert len(current_records(session, first.id, org_id)) == 2


def test_closed_versions_and_people_cannot_supply_a_match(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca", "kind": "person"},
        ],
    )
    records[0].valid_to = START + timedelta(days=1)
    session.flush()
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult()
    assert all(session.get(OrgLink, record.id) is None for record in records)


def test_parent_domain_with_distinct_company_profiles_needs_review(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {"domain": "group.ca", "linkedin": "company/parent"},
            {"domain": "group.ca", "linkedin": "company/subsidiary"},
        ],
    )
    # Act
    result = match_organizations(session, tenant.id)
    saved = session.scalar(select(MatchCandidate).where(MatchCandidate.tenant_id == tenant.id))
    # Assert
    assert result == MatchResult(0, 0, 1) and saved.status == "pending"
    assert saved.matched_fields[0]["field"] == "domain"
    assert all(session.get(OrgLink, record.id) is None for record in records)


def test_caller_can_roll_back_all_matching_writes(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [{"domain": "exemple.ca"}] * 2)
    transaction = session.begin_nested()
    # Act
    result = match_organizations(session, tenant.id)
    transaction.rollback()
    # Assert
    assert result == MatchResult(1, 2, 0)
    assert all(session.get(OrgLink, record.id) is None for record in records)
    assert session.scalar(select(Organization).where(Organization.tenant_id == tenant.id)) is None

# AI contribution: 50% or more AI-generated
"""Matching decisions and version carry-forward against PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import MatchCandidate, Organization, OrgLink, Source, SourceRecord, SyncRun, Tenant
from app.processing.entity_resolution import MatchResult, match_organizations
from app.processing.normalization import normalize_organization_name
from app.services.organizations import current_records, surviving_organization_id
from app.services.source_records import close_unseen, upsert_records

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


def rule_link(session, records, organization=None):
    """Link records to one organization as an earlier Tier 1 run would have."""
    if organization is None:
        organization = Organization(tenant_id=records[0].tenant_id, display_name="Old name")
        session.add(organization)
        session.flush()
    for record in records:
        session.add(
            OrgLink(
                tenant_id=record.tenant_id,
                source_record_id=record.id,
                organization_id=organization.id,
                method="domain",
                confidence=1,
                decided_by="tier1:tier1-v1",
                evidence=[],
            )
        )
    session.flush()
    return organization


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


def test_manual_organizations_are_not_merged(session):
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
    links = [session.get(OrgLink, record.id) for record in records]
    # Assert: the domain pair is still linked, the LinkedIn link to the rejected record is not.
    assert result == MatchResult(1, 2, 1)
    assert decision.status == "rejected"
    assert links[0].organization_id == links[1].organization_id and links[2] is None
    saved = session.scalar(
        select(MatchCandidate).where(
            MatchCandidate.tenant_id == tenant.id, MatchCandidate.status == "pending"
        )
    )
    assert {saved.left_record_id, saved.right_record_id} == {records[1].id, records[2].id}
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


def named(name):
    return {"name_normalized": normalize_organization_name(name)}


def chained_organizations(session):
    """Record 0 in one organization, 1 and 2 in another; 3 and 4 chain them together."""
    tenant, run, mapped, records = setup_records(
        session,
        [
            {"domain": "exemple.ca", "attributes": {"name": "Exemple"}, **named("Exemple")},
            {
                "linkedin": "company/exemple",
                "attributes": {"name": "Exemple Inc."},
                **named("Exemple Inc."),
            },
            {"linkedin": "company/exemple", "attributes": {"name": "Exemple"}, **named("Exemple")},
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca", "linkedin": "company/exemple"},
        ],
    )
    small = rule_link(session, [records[0]])
    large = rule_link(session, records[1:3])
    older = Organization(tenant_id=tenant.id, display_name="Older", merged_into=small.id)
    session.add(older)
    session.flush()
    return tenant, run, mapped, records, small, large, older


def test_chain_merges_organizations_and_old_ids_still_resolve(session):
    # Arrange
    tenant, _, _, records, small, large, older = chained_organizations(session)
    # Act
    first = match_organizations(session, tenant.id)
    session.expire_all()
    second = match_organizations(session, tenant.id)
    links = [session.get(OrgLink, record.id) for record in records]
    # Assert: the organization with more links survives, and merged_into stays one hop.
    assert first == MatchResult(0, 2, 0, 1) and second == MatchResult()
    assert {link.organization_id for link in links} == {large.id}
    assert (small.merged_into, older.merged_into, large.merged_into) == (large.id, large.id, None)
    assert (large.display_name, large.primary_domain) == ("Exemple", "exemple.ca")
    ids = (small.id, older.id, large.id)
    assert {surviving_organization_id(session, tenant.id, i) for i in ids} == {large.id}


def test_merge_leaves_closed_versions_on_the_old_organization(session):
    # Arrange
    tenant, run, mapped, records, small, large, _ = chained_organizations(session)
    next_run = SyncRun(
        tenant_id=tenant.id, source_id=run.source_id, started_at=START + timedelta(days=1)
    )
    session.add(next_run)
    session.flush()
    upsert_records(session, next_run, [replace(mapped[0], attributes={"name": "Renamed"})])
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result.organizations_merged == 1
    assert session.get(OrgLink, records[0].id).organization_id == small.id
    members = current_records(session, tenant.id, large.id)
    assert len(members) == 5 and records[0].id not in {member.id for member in members}


def test_merge_is_rolled_back_with_the_callers_transaction(session):
    # Arrange
    tenant, _, _, records, small, _, _ = chained_organizations(session)
    transaction = session.begin_nested()
    # Act
    result = match_organizations(session, tenant.id)
    transaction.rollback()
    session.expire_all()
    # Assert
    assert result.organizations_merged == 1
    assert session.get(Organization, small.id).merged_into is None
    assert session.get(OrgLink, records[0].id).organization_id == small.id
    assert session.get(OrgLink, records[4].id) is None


def test_conflicting_chain_links_the_clean_part_and_sets_aside_the_rest(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {"domain": "parent.ca", "linkedin": "company/group"},
            {"domain": "parent.ca"},
            {"domain": "subsidiary.ca", "linkedin": "company/group"},
        ],
    )
    # Act
    result = match_organizations(session, tenant.id)
    links = [session.get(OrgLink, record.id) for record in records]
    saved = session.scalar(select(MatchCandidate).where(MatchCandidate.tenant_id == tenant.id))
    # Assert
    assert result == MatchResult(1, 2, 1)
    assert links[0].organization_id == links[1].organization_id and links[2] is None
    assert saved.matched_fields[0]["field"] == "linkedin"
    organization = session.get(Organization, links[0].organization_id)
    assert organization.primary_domain == "parent.ca"
    assert match_organizations(session, tenant.id) == MatchResult()


def test_merged_ids_do_not_resolve_from_another_tenant(session):
    # Arrange
    tenant, _, _, _, small, large, _ = chained_organizations(session)
    other, _, _, _ = setup_records(session, [{"domain": "exemple.ca"}])
    # Act
    match_organizations(session, tenant.id)
    match_organizations(session, other.id)
    # Assert
    assert surviving_organization_id(session, other.id, small.id) is None
    assert surviving_organization_id(session, tenant.id, small.id) == large.id
    assert current_records(session, other.id, large.id) == []


def test_manual_organization_with_two_domains_still_gains_a_matching_record(session):
    # Arrange: a reviewer put old.ca and new.ca in one organization.
    tenant, _, _, records = setup_records(
        session, [{"domain": "old.ca"}, {"domain": "new.ca"}, {"domain": "new.ca"}]
    )
    first = manual_link(session, records[0])
    session.add(
        OrgLink(
            tenant_id=tenant.id,
            source_record_id=records[1].id,
            organization_id=first.organization_id,
            method="manual",
            confidence=1,
            decided_by="reviewer",
            evidence=[{"decided_by": "reviewer"}],
        )
    )
    session.flush()
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult(0, 1, 0)
    assert session.get(OrgLink, records[2].id).organization_id == first.organization_id


def test_member_whose_domain_changed_still_brings_in_records_on_the_new_domain(session):
    # Arrange
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca"}, {"domain": "exemple.ca"}]
    )
    match_organizations(session, tenant.id)
    organization_id = session.get(OrgLink, records[0].id).organization_id
    next_run = SyncRun(
        tenant_id=tenant.id, source_id=run.source_id, started_at=START + timedelta(days=1)
    )
    session.add(next_run)
    session.flush()
    newcomer = MappedRecord(
        kind="organization",
        external_id="9",
        workspace_id=None,
        name_normalized="org 9",
        domain="nouveau.ca",
    )
    upsert_records(session, next_run, [replace(mapped[1], domain="nouveau.ca"), newcomer])
    # Act
    result = match_organizations(session, tenant.id)
    added = session.scalar(
        select(SourceRecord).where(
            SourceRecord.tenant_id == tenant.id, SourceRecord.external_id == "9"
        )
    )
    # Assert
    assert result == MatchResult(0, 1, 0)
    assert session.get(OrgLink, added.id).organization_id == organization_id


def test_link_left_on_a_merged_organization_is_moved_to_the_survivor(session):
    # Arrange: a sync copied a link onto the merged organization after the merge.
    tenant, run, _, _, small, large, _ = chained_organizations(session)
    match_organizations(session, tenant.id)
    newcomer = MappedRecord(
        kind="organization", external_id="9", workspace_id=None, name_normalized="org 9"
    )
    upsert_records(session, run, [newcomer])
    added = session.scalar(
        select(SourceRecord).where(
            SourceRecord.tenant_id == tenant.id, SourceRecord.external_id == "9"
        )
    )
    rule_link(session, [added], small)
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result == MatchResult()
    assert session.get(OrgLink, added.id).organization_id == large.id


def test_record_back_after_a_merge_gets_the_survivors_link(session):
    # Arrange: records 0 and 3 in one organization, 1 and 2 in another, 4 joins them.
    tenant, run, mapped, records = setup_records(
        session,
        [
            {"domain": "exemple.ca"},
            {"linkedin": "company/exemple"},
            {"linkedin": "company/exemple"},
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca", "linkedin": "company/exemple"},
        ],
    )
    small = rule_link(session, [records[0], records[3]])
    large = rule_link(session, records[1:3])
    runs = []
    for day in (1, 2):
        later = SyncRun(
            tenant_id=tenant.id, source_id=run.source_id, started_at=START + timedelta(days=day)
        )
        session.add(later)
        session.flush()
        runs.append(later)
    upsert_records(session, runs[0], mapped[1:])
    close_unseen(session, runs[0])
    match_organizations(session, tenant.id)
    # Act
    upsert_records(session, runs[1], [mapped[0]])
    session.expire_all()
    back = session.scalar(
        select(SourceRecord).where(
            SourceRecord.tenant_id == tenant.id,
            SourceRecord.external_id == "0",
            SourceRecord.valid_to.is_(None),
        )
    )
    # Assert
    assert session.get(Organization, small.id).merged_into == large.id
    assert session.get(OrgLink, back.id).organization_id == large.id
    assert back.id in {member.id for member in current_records(session, tenant.id, large.id)}


def test_manual_link_on_a_removed_record_still_protects_its_organization(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {"domain": "exemple.ca"},
            {"linkedin": "company/exemple"},
            {"domain": "exemple.ca"},
            {"domain": "exemple.ca", "linkedin": "company/exemple"},
        ],
    )
    reviewed = manual_link(session, records[0])
    rule_link(session, [records[2]], session.get(Organization, reviewed.organization_id))
    other = rule_link(session, [records[1]])
    records[0].valid_to = START + timedelta(days=1)
    session.flush()
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult(0, 1, 1, 0)
    assert session.get(Organization, other.id).merged_into is None
    assert session.get(Organization, reviewed.organization_id).merged_into is None


def test_reviewers_name_is_kept_on_every_run(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session, [{"domain": "exemple.ca", "attributes": {"name": "Exemple"}}] * 2
    )
    link = manual_link(session, records[0])
    organization = session.get(Organization, link.organization_id)
    # Act
    first = match_organizations(session, tenant.id)
    second = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert (first, second) == (MatchResult(0, 1, 0), MatchResult())
    assert (organization.display_name, organization.primary_domain) == ("Verified company", None)


def test_renamed_members_rename_their_organization_but_keep_its_domain(session):
    # Arrange
    tenant, run, mapped, records = setup_records(
        session, [{"domain": "exemple.ca", "attributes": {"name": "Old name"}}] * 2
    )
    match_organizations(session, tenant.id)
    organization = session.get(Organization, session.get(OrgLink, records[0].id).organization_id)
    next_run = SyncRun(
        tenant_id=tenant.id, source_id=run.source_id, started_at=START + timedelta(days=1)
    )
    session.add(next_run)
    session.flush()
    renamed = [replace(item, domain=None, attributes={"name": "New name"}) for item in mapped]
    upsert_records(session, next_run, renamed)
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result == MatchResult()
    assert (organization.display_name, organization.primary_domain) == ("New name", "exemple.ca")


def test_weak_match_inside_one_organization_is_not_set_aside(session):
    # Arrange: the same name in two cities, both already in one organization.
    tenant, _, _, records = setup_records(
        session,
        [
            {"name_normalized": "exemple", "city": "quebec"},
            {"name_normalized": "exemple", "city": "montreal"},
        ],
    )
    rule_link(session, records)
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult()
    assert (
        session.scalar(select(MatchCandidate).where(MatchCandidate.tenant_id == tenant.id)) is None
    )


def test_old_organization_id_lists_the_survivors_records(session):
    # Arrange
    tenant, _, _, records, _, large, older = chained_organizations(session)
    match_organizations(session, tenant.id)
    # Act
    by_old_id = current_records(session, tenant.id, older.id)
    # Assert
    assert by_old_id == current_records(session, tenant.id, large.id)
    assert {record.id for record in by_old_id} == {record.id for record in records}


def test_link_to_a_merged_organization_reaches_a_survivor_with_no_current_links(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [{"domain": "exemple.ca"}])
    survivor = Organization(tenant_id=tenant.id, display_name="Survivor")
    session.add(survivor)
    session.flush()
    merged = Organization(tenant_id=tenant.id, display_name="Merged", merged_into=survivor.id)
    session.add(merged)
    session.flush()
    rule_link(session, records, merged)
    # Act
    result = match_organizations(session, tenant.id)
    session.expire_all()
    # Assert
    assert result == MatchResult()
    assert session.get(OrgLink, records[0].id).organization_id == survivor.id
    assert session.get(Organization, survivor.id).primary_domain == "exemple.ca"

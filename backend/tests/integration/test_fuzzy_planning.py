# AI contribution: 50% or more AI-generated
"""What fuzzy links may and may not do to organizations, against PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import Organization, OrgLink, SourceRecord, SyncRun
from app.processing.entity_resolution import MatchResult, match_organizations
from app.services.source_records import upsert_records
from tests.integration.test_entity_resolution import START, setup_records
from tests.integration.test_fuzzy_matching import TREMBLAI, TREMBLAY, add_record

pytestmark = pytest.mark.integration

ROY = {"name_normalized": "imprimerie roy", "city": "levis", "external_ids": {"crm": ["2"]}}


def link(session, record):
    return session.get(OrgLink, record.id)


def next_run(session, run, days):
    later = SyncRun(
        tenant_id=run.tenant_id, source_id=run.source_id, started_at=START + timedelta(days=days)
    )
    session.add(later)
    session.flush()
    return later


def current(session, tenant, external_id):
    return session.scalar(
        select(SourceRecord).where(
            SourceRecord.tenant_id == tenant.id,
            SourceRecord.external_id == external_id,
            SourceRecord.valid_to.is_(None),
        )
    )


def two_organizations_and_a_fuzzy_member(session):
    """O1 holds two records on a.ca and B by a fuzzy link; O2 two records on crm 2."""
    tenant, run, mapped, records = setup_records(
        session,
        [
            {**TREMBLAY, "domain": "a.ca"},
            {**TREMBLAY, "domain": "a.ca"},
            {**TREMBLAI, "linkedin": "company/q"},
            ROY,
            ROY,
        ],
    )
    match_organizations(session, tenant.id)
    return tenant, run, mapped, records


def test_fuzzy_edges_never_merge_two_tier1_groups(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session,
        [
            {**TREMBLAY, "domain": "a.ca"},
            {**TREMBLAY, "domain": "a.ca"},
            {**TREMBLAI, "linkedin": "company/b"},
            {**TREMBLAI, "linkedin": "company/b"},
        ],
    )
    # Act
    first = match_organizations(session, tenant.id)
    second = match_organizations(session, tenant.id)
    # Assert
    assert first == MatchResult(2, 4, 4) and second == MatchResult()
    assert len({link(session, record).organization_id for record in records}) == 2
    assert {link(session, record).method for record in records} == {"domain", "linkedin"}


def test_a_new_record_cannot_merge_organizations_through_a_fuzzy_member(session):
    # Arrange
    tenant, run, _, records = two_organizations_and_a_fuzzy_member(session)
    fuzzy_method = link(session, records[2]).method
    bridge = {
        "name_normalized": "zz bridge",
        "linkedin": "company/q",
        "external_ids": {"crm": ["2"]},
    }
    x = add_record(session, run, "x", bridge)
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert fuzzy_method == "fuzzy"
    assert result.organizations_merged == 0 and result.candidates_created >= 1
    assert link(session, x).organization_id == link(session, records[2]).organization_id
    assert link(session, x).evidence[0]["tentative"] is True


def test_a_record_placed_through_a_fuzzy_member_cannot_merge_organizations_later(session):
    # Arrange
    tenant, run, mapped, _ = two_organizations_and_a_fuzzy_member(session)
    bridge = {"name_normalized": "zz bridge", "linkedin": "company/q"}
    x = add_record(session, run, "x", bridge)
    second = match_organizations(session, tenant.id)
    method = link(session, x).method
    # A new version of X adds O2's crm id; versioning copies X's link and its evidence.
    later = next_run(session, run, 1)
    upsert_records(
        session,
        later,
        [*mapped, MappedRecord("organization", "x", None, **bridge, external_ids={"crm": ["2"]})],
    )
    # Act
    third = match_organizations(session, tenant.id)
    # Assert
    assert second.links_created == 1 and method == "linkedin"
    assert third.organizations_merged == 0
    organizations = session.scalars(select(Organization).where(Organization.tenant_id == tenant.id))
    assert all(organization.merged_into is None for organization in organizations)
    assert link(session, current(session, tenant, "x")).evidence[0]["tentative"] is True


def test_tier1_records_keep_tier1_links_whatever_the_record_ids(session):
    # Arrange
    orders = {}
    for _ in range(60):
        tenant, _, _, records = setup_records(
            session,
            [{**TREMBLAY, "domain": "t.ca"}, {**TREMBLAY, "domain": "t.ca"}, TREMBLAI],
        )
        p, q, x = records
        orders.setdefault(x.id < min(p.id, q.id), (tenant, records))
        if len(orders) == 2:
            break
    # Act
    for tenant, _ in orders.values():
        match_organizations(session, tenant.id)
    # Assert
    assert len(orders) == 2
    for _, (p, q, x) in orders.values():
        assert [link(session, r).method for r in (p, q, x)] == ["domain", "domain", "fuzzy"]
        assert link(session, p).decided_by == "tier1:tier1-v1"


@pytest.mark.parametrize("syncs", [1, 2])
def test_the_result_depends_on_whether_records_arrive_together(session, syncs):
    """X and Y share a domain, F and Z a LinkedIn page, X and F are fuzzy: one sync keeps two
    organizations; two syncs place F with X, then Z through F, both tentative."""
    # Arrange
    x = {**TREMBLAY, "domain": "x.ca"}
    y = {**TREMBLAY, "domain": "x.ca"}
    f = {**TREMBLAI, "linkedin": "company/f"}
    z = {"name_normalized": "zeta", "linkedin": "company/f"}
    first_batch = [x, y, f, z] if syncs == 1 else [x, y, f]
    tenant, run, _, records = setup_records(session, first_batch)
    match_organizations(session, tenant.id)
    if syncs == 2:
        records.append(add_record(session, run, "z", z))
    # Act
    match_organizations(session, tenant.id)
    # Assert
    organizations = {link(session, record).organization_id for record in records}
    if syncs == 1:
        assert len(organizations) == 2
    else:
        assert len(organizations) == 1
        assert all(e.get("tentative") for e in link(session, records[3]).evidence)

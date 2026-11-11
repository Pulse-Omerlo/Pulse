# AI contribution: 50% or more AI-generated
"""Tier 2 fuzzy matches saved by match_organizations against PostgreSQL."""

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import MatchCandidate, OrgLink, SourceRecord
from app.processing.entity_resolution import RULE_WEIGHTS, MatchResult, match_organizations
from app.services.source_records import upsert_records
from tests.integration.test_entity_resolution import (
    candidate,
    manual_link,
    setup_records,
)

pytestmark = pytest.mark.integration

TREMBLAY = {"name_normalized": "boulangerie tremblay", "city": "quebec"}
TREMBLAI = {"name_normalized": "boulangerie tremblai", "city": "quebec"}
LOWEST_TIER1_WEIGHT = min(RULE_WEIGHTS[rule] for rule in ("domain", "linkedin", "name_city"))


def candidates(session, tenant):
    return list(
        session.scalars(select(MatchCandidate).where(MatchCandidate.tenant_id == tenant.id))
    )


def test_similar_names_in_one_city_are_linked_once(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [TREMBLAY, TREMBLAI])
    # Act
    first = match_organizations(session, tenant.id)
    session.expire_all()
    second = match_organizations(session, tenant.id)
    links = [session.get(OrgLink, record.id) for record in records]
    # Assert
    assert first == MatchResult(1, 2, 0) and second == MatchResult()
    assert links[0].organization_id == links[1].organization_id
    for link in links:
        assert link.method == "fuzzy" and link.decided_by == "tier2:tier2-v1"
        assert 0 < link.confidence < LOWEST_TIER1_WEIGHT
    fields = {entry["field"]: entry for entry in links[0].evidence}
    assert fields["name_similarity"]["value"] == "0.875"
    assert fields["city"]["value"] == "quebec"
    assert fields["name_normalized"]["value"] == "boulangerie tremblay"
    assert fields["name_normalized"]["matched_value"] == "boulangerie tremblai"
    assert fields["name_normalized"]["matched_record_id"] == str(records[1].id)
    assert {entry["rule_set_version"] for entry in links[1].evidence} == {"tier2-v1"}


def test_similar_names_without_a_secondary_signal_wait_for_review(session):
    # Arrange
    tenant, _, _, records = setup_records(
        session, [{**TREMBLAY, "city": None}, {**TREMBLAI, "city": None}]
    )
    # Act
    first = match_organizations(session, tenant.id)
    second = match_organizations(session, tenant.id)
    [saved] = candidates(session, tenant)
    # Assert
    assert first == MatchResult(0, 0, 1) and second == MatchResult()
    assert saved.status == "pending" and saved.score == 0.875
    assert {entry["rule"] for entry in saved.matched_fields} == {"fuzzy_name"}
    names = next(e for e in saved.matched_fields if e["field"] == "name_normalized")
    left, right = sorted(records, key=lambda record: record.id)
    assert (names["value"], names["matched_value"]) == (left.name_normalized, right.name_normalized)
    assert names["matched_record_id"] == str(right.id)
    assert all(session.get(OrgLink, record.id) is None for record in records)


def test_rejected_fuzzy_pair_is_never_linked_or_proposed_again(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [TREMBLAY, TREMBLAI])
    decision = candidate(session, records, "rejected")
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult()
    assert candidates(session, tenant) == [decision] and decision.status == "rejected"
    assert all(session.get(OrgLink, record.id) is None for record in records)


def add_record(session, run, external_id, fields):
    """One more record from the same source, after a first matching run."""
    mapped = MappedRecord(kind="organization", external_id=external_id, workspace_id=None, **fields)
    upsert_records(session, run, [mapped])
    return session.scalar(select(SourceRecord).where(SourceRecord.external_id == external_id))


def link_state(session, records):
    links = [session.get(OrgLink, record.id) for record in records]
    return [(link.method, link.organization_id, link.decided_at, link.evidence) for link in links]


def test_fuzzy_match_joins_an_existing_organization_without_moving_its_links(session):
    # Arrange
    same_domain = {**TREMBLAY, "domain": "tremblay.ca"}
    tenant, run, _, records = setup_records(session, [same_domain, same_domain])
    match_organizations(session, tenant.id)
    before = link_state(session, records)
    late = add_record(session, run, "late", TREMBLAI)
    # Act
    result = match_organizations(session, tenant.id)
    link = session.get(OrgLink, late.id)
    # Assert
    assert result == MatchResult(0, 1, 0)
    assert (link.method, link.organization_id) == ("fuzzy", before[0][1])
    assert link_state(session, records) == before


def test_manual_links_are_untouched_by_fuzzy_matches(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [TREMBLAY, TREMBLAI])
    manual = [manual_link(session, record) for record in records]
    before = link_state(session, records)
    # Act
    result = match_organizations(session, tenant.id)
    [saved] = candidates(session, tenant)
    # Assert
    assert result == MatchResult(0, 0, 1) and saved.score == 0.875
    assert link_state(session, records) == before
    assert {link.method for link in manual} == {"manual"}


def test_manual_link_is_joined_by_an_unlinked_similar_record(session):
    # Arrange
    tenant, _, _, records = setup_records(session, [TREMBLAY, TREMBLAI])
    manual = manual_link(session, records[0])
    snapshot = (manual.organization_id, manual.evidence, manual.decided_at)
    # Act
    result = match_organizations(session, tenant.id)
    # Assert
    assert result == MatchResult(0, 1, 0)
    assert (manual.organization_id, manual.evidence, manual.decided_at) == snapshot
    assert session.get(OrgLink, records[1].id).organization_id == manual.organization_id

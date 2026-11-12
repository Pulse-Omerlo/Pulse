# AI contribution: 50% or more AI-generated
"""People matching decisions against PostgreSQL."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ingestion.mapped import MappedRecord
from app.models import MatchCandidate, Person, PersonLink, Source, SourceRecord, SyncRun, Tenant
from app.processing.person_resolution import PersonMatchResult, match_people
from app.services.source_records import upsert_records

pytestmark = pytest.mark.integration
START = datetime(2026, 11, 1, tzinfo=UTC)


def setup_people(session, fields):
    tenant = Tenant(name="People matching test")
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
                    "kind": "person",
                    "external_id": str(n),
                    "workspace_id": None,
                    "name_normalized": f"person {n}",
                    "attributes": {"identifiers": {}},
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
    return tenant, run, records


def test_email_links_people_and_rerunning_is_idempotent(session):
    # Arrange
    identifiers = {"identifiers": {"emails": ["shared@example.com"]}}
    tenant, _, records = setup_people(
        session,
        [
            {"name_normalized": "marie tremblay", "attributes": identifiers},
            {"name_normalized": "marie tremblay", "attributes": identifiers},
        ],
    )

    # Act
    first = match_people(session, tenant.id)
    session.expire_all()
    second = match_people(session, tenant.id)
    links = [session.get(PersonLink, record.id) for record in records]

    # Assert
    assert first == PersonMatchResult(1, 2, 0, 1)
    assert second == PersonMatchResult()
    assert links[0].person_id == links[1].person_id
    assert links[0].method == "email"
    assert links[0].evidence[0]["rule_set_version"] == "tier1-v1"


def test_rejected_candidate_blocks_a_strong_merge(session):
    # Arrange
    identifiers = {"identifiers": {"emails": ["shared@example.com"]}}
    tenant, _, records = setup_people(
        session,
        [
            {"name_normalized": "marie tremblay", "attributes": identifiers},
            {"name_normalized": "marie tremblay", "attributes": identifiers},
        ],
    )
    left, right = sorted(record.id for record in records)
    session.add(
        MatchCandidate(
            tenant_id=tenant.id,
            left_record_id=left,
            right_record_id=right,
            score=1.0,
            status="rejected",
            matched_fields=[],
        )
    )
    session.flush()

    # Act
    result = match_people(session, tenant.id)
    links = [session.get(PersonLink, record.id) for record in records]

    # Assert
    assert result.links_created == 0
    assert result.candidates_created == 0
    assert links == [None, None]


def test_changed_ids_leaves_unrelated_people_alone(session):
    # Arrange
    tenant, _, records = setup_people(
        session,
        [
            {
                "name_normalized": "marie",
                "attributes": {"identifiers": {"emails": ["a@example.com"]}},
            },
            {
                "name_normalized": "marie",
                "attributes": {"identifiers": {"emails": ["a@example.com"]}},
            },
            {
                "name_normalized": "paul",
                "attributes": {"identifiers": {"emails": ["b@example.com"]}},
            },
            {
                "name_normalized": "paul",
                "attributes": {"identifiers": {"emails": ["b@example.com"]}},
            },
        ],
    )
    changed = {records[0].id, records[1].id}

    # Act
    result = match_people(session, tenant.id, changed_ids=changed)
    people = session.scalars(select(Person)).all()
    linked = [session.get(PersonLink, record.id) is not None for record in records]

    # Assert
    assert result.people_created == 1
    assert result.links_created == 2
    assert linked == [True, True, False, False]
    assert len(people) == 1

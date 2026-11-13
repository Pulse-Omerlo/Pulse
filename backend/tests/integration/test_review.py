# AI contribution: 50% or more AI-generated
"""Accepting, rejecting and undoing possible matches in the review queue."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.tenancy import bound_to_tenant, system_access
from app.models import MatchCandidate, Organization, OrgLink, Source, SourceRecord, SyncRun, Tenant
from app.processing.entity_resolution import match_organizations
from app.services import review
from app.services.pagination import Paging
from tests.integration.signin import signed_in_client

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, tzinfo=UTC)
REVIEWER = "ana@pulse.test"


class Network:
    def __init__(self, session):
        self.session = session
        with system_access(session):
            self.tenant = Tenant(name="Review network")
            session.add(self.tenant)
            session.flush()
            self.source = Source(tenant_id=self.tenant.id, kind="csv")
            session.add(self.source)
            session.flush()
            self.run = SyncRun(tenant_id=self.tenant.id, source_id=self.source.id, started_at=T0)
            session.add(self.run)
            session.commit()

    def record(self, external_id: str, name: str, domain: str | None = None, kind="organization"):
        record = SourceRecord(
            tenant_id=self.tenant.id,
            source_id=self.source.id,
            external_id=external_id,
            kind=kind,
            name_normalized=name.lower(),
            domain=domain,
            first_seen_run=self.run.id,
            last_seen_run=self.run.id,
            valid_from=T0,
            attributes={"name": name, "website": f"https://www.{domain}/" if domain else None},
        )
        self.session.add(record)
        self.session.flush()
        return record

    def organization(self, name: str, *records: SourceRecord) -> Organization:
        organization = Organization(tenant_id=self.tenant.id, display_name=name)
        self.session.add(organization)
        self.session.flush()
        for record in records:
            self.session.add(
                OrgLink(
                    tenant_id=self.tenant.id,
                    source_record_id=record.id,
                    organization_id=organization.id,
                    method="domain",
                    confidence=1.0,
                    decided_by="tier1",
                )
            )
        self.session.flush()
        return organization

    def candidate(self, first: SourceRecord, second: SourceRecord, score=0.7) -> MatchCandidate:
        left, right = sorted([first.id, second.id])
        candidate = MatchCandidate(
            tenant_id=self.tenant.id,
            left_record_id=left,
            right_record_id=right,
            score=score,
            matched_fields=[{"rule": "fuzzy", "field": "name"}],
        )
        self.session.add(candidate)
        self.session.commit()
        return candidate

    def bound(self):
        return bound_to_tenant(self.session, self.tenant.id)

    def organization_of(self, record: SourceRecord) -> uuid.UUID | None:
        link = self.session.get(OrgLink, record.id)
        if link is None:
            return None
        organization = self.session.get(Organization, link.organization_id)
        return organization.merged_into or organization.id


@pytest.fixture
def net(session):
    return Network(session)


def test_accepting_two_loose_records_creates_one_organization(net, session):
    # Arrange
    a, b = net.record("o1", "Atelier Boréal", "boreal.ca"), net.record("o2", "Atelier Boreal")
    candidate = net.candidate(a, b)

    # Act
    with net.bound():
        review.accept(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

    # Assert
    with net.bound():
        assert net.organization_of(a) == net.organization_of(b) is not None
        links = [session.get(OrgLink, r.id) for r in (a, b)]
        assert {(link.method, link.decided_by) for link in links} == {("manual", REVIEWER)}
        organization = session.get(Organization, net.organization_of(a))
        # Named after the pair's first record, whichever that is.
        assert organization.display_name in {"Atelier Boréal", "Atelier Boreal"}
        assert organization.primary_domain == "boreal.ca"
        assert (candidate.status, candidate.decided_by) == ("accepted", REVIEWER)
        assert candidate.decided_at is not None


def test_accepting_a_loose_record_adds_it_to_the_other_ones_organization(net, session):
    # Arrange
    a, b = net.record("o1", "Acme", "acme.ca"), net.record("o2", "Acme inc")
    acme = net.organization("Acme", a)
    candidate = net.candidate(a, b)

    # Act
    with net.bound():
        review.accept(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

        # Assert
        assert net.organization_of(b) == acme.id
        assert session.get(OrgLink, b.id).method == "manual"


def test_accepting_records_of_two_organizations_merges_the_smaller_one(net, session):
    # Arrange
    a1, a2 = net.record("a1", "Acme", "acme.ca"), net.record("a2", "Acme Qc", "acme.ca")
    b = net.record("b1", "Acme Quebec", "acmeqc.ca")
    big, small = net.organization("Acme", a1, a2), net.organization("Acme Quebec", b)
    candidate = net.candidate(a2, b)

    # Act
    with net.bound():
        review.accept(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

        # Assert
        assert small.merged_into == big.id
        assert session.get(OrgLink, b.id).organization_id == big.id
        assert candidate.decision["merged"]["organization"] == str(small.id)


def test_undoing_a_merge_puts_both_organizations_back(net, session):
    # Arrange
    a = net.record("a1", "Acme", "acme.ca")
    b1, b2 = net.record("b1", "Acme Quebec", "acmeqc.ca"), net.record("b2", "Acme Qc")
    # Accepting merges the one-record organization into the two-record one.
    small, big = net.organization("Acme", a), net.organization("Acme Quebec", b1, b2)
    candidate = net.candidate(a, b1)
    with net.bound():
        review.accept(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

    # Act
    with net.bound():
        review.undo(session, net.tenant.id, candidate.id)
        session.commit()

        # Assert
        assert big.merged_into is None and small.merged_into is None
        assert session.get(OrgLink, a.id).organization_id == small.id
        assert {session.get(OrgLink, r.id).organization_id for r in (b1, b2)} == {big.id}
        assert candidate.decision is None
        assert (candidate.status, candidate.decided_by, candidate.decision) == (
            "pending",
            None,
            None,
        )


def test_undoing_an_accept_that_created_an_organization_removes_it(net, session):
    # Arrange
    a, b = net.record("o1", "Atelier Boréal"), net.record("o2", "Atelier Boreal")
    candidate = net.candidate(a, b)
    with net.bound():
        review.accept(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()
        created = net.organization_of(a)

    # Act
    with net.bound():
        review.undo(session, net.tenant.id, candidate.id)
        session.commit()

        # Assert
        assert session.get(OrgLink, a.id) is None and session.get(OrgLink, b.id) is None
        assert session.get(Organization, created) is None


def test_a_rejected_pair_is_never_joined_or_proposed_again(net, session):
    # Arrange
    a, b = net.record("o1", "Acme", "acme.ca"), net.record("o2", "Acme Retail", "acme.ca")
    candidate = net.candidate(a, b)
    with net.bound():
        review.reject(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

    # Act
    match_organizations(session, net.tenant.id)
    session.commit()

    # Assert
    with net.bound():
        first, second = net.organization_of(a), net.organization_of(b)
        assert first is None or first != second
        assert review.review_queue(session, net.tenant.id, Paging(1, 50)) == ([], 0)
        assert (candidate.status, candidate.decided_by) == ("rejected", REVIEWER)


def test_a_decided_pair_cannot_be_decided_again(net, session):
    # Arrange
    a, b = net.record("o1", "Acme"), net.record("o2", "Acme inc")
    candidate = net.candidate(a, b)
    with net.bound():
        review.reject(session, net.tenant.id, candidate.id, REVIEWER)
        session.commit()

        # Act / Assert
        with pytest.raises(ConflictError):
            review.accept(session, net.tenant.id, candidate.id, REVIEWER)


def test_the_queue_holds_only_current_organization_pairs_by_score(net, session):
    # Arrange
    a, b, c = (net.record(f"o{i}", f"Acme {i}") for i in range(3))
    low, high = net.candidate(a, b, score=0.5), net.candidate(a, c, score=0.9)
    p, q = net.record("p1", "Ana Roy", kind="person"), net.record("p2", "Ana Roy", kind="person")
    net.candidate(p, q, score=1.0)
    old = net.record("o9", "Acme old")
    old.valid_to = T0.replace(day=2)
    net.candidate(a, old, score=0.99)

    # Act
    with net.bound():
        rows, total = review.review_queue(session, net.tenant.id, Paging(1, 50))

    # Assert
    assert [candidate.id for candidate, _, _ in rows] == [high.id, low.id]
    assert total == 2


def test_the_api_records_the_signed_in_reviewer_and_shows_values_side_by_side(net, session):
    # Arrange
    a, b = net.record("o1", "Acme", "acme.ca"), net.record("o2", "ACME inc", "acme.ca")
    candidate = net.candidate(a, b)
    base = f"/api/tenants/{net.tenant.id}/duplicates/candidates"

    with signed_in_client(session, net.tenant.id) as api:
        # Act
        queue = api.get(base).json()
        accepted = api.post(f"{base}/{candidate.id}/accept")
        again = api.post(f"{base}/{candidate.id}/reject")
        decided = api.get(base, params={"status": "accepted"}).json()
        undone = api.post(f"{base}/{candidate.id}/undo")

    # Assert
    (item,) = queue["items"]
    website = next(v for v in item["left_values"] if v["field"] == "website")
    assert website["normalized"] == "acme.ca"
    assert accepted.status_code == 200
    assert accepted.json()["decided_by"].endswith("@pulse.test")
    assert again.status_code == 409
    assert [i["id"] for i in decided["items"]] == [str(candidate.id)]
    assert undone.json()["status"] == "pending"
    with net.bound():
        assert session.scalars(select(OrgLink)).all() == []

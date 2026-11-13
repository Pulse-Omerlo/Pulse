# AI contribution: 50% or more AI-generated
"""People are linked to organizations in Postgres, checked again, and placed in the sector mix."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.browse import database
from app.core.tenancy import bound_to_tenant
from app.main import app
from app.models import (
    Affiliation,
    IndustryClassification,
    Organization,
    OrgLink,
    Source,
    SourceRecord,
    SyncRun,
    Tenant,
)
from app.services.affiliations import AffiliationReport, link_people, linked_people
from app.services.sector_mix import measure_sector_mix

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = datetime(2026, 11, 2, tzinfo=UTC)


class Network:
    def __init__(self, session, name: str = "Affiliation network"):
        self.session = session
        self.tenant = Tenant(name=name)
        session.add(self.tenant)
        session.flush()
        self.source = Source(tenant_id=self.tenant.id, kind="csv")
        session.add(self.source)
        session.flush()
        self.run = SyncRun(tenant_id=self.tenant.id, source_id=self.source.id, started_at=T0)
        session.add(self.run)
        session.flush()

    def record(self, kind: str, external_id: str, valid_from=T0, **fields) -> SourceRecord:
        record = SourceRecord(
            tenant_id=self.tenant.id,
            source_id=self.source.id,
            external_id=external_id,
            kind=kind,
            first_seen_run=self.run.id,
            last_seen_run=self.run.id,
            valid_from=valid_from,
            **fields,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def organization(self, external_id: str, name: str, domain: str, sector: str = "54"):
        record = self.record(
            "organization",
            external_id,
            name_normalized=name.lower(),
            domain=domain,
            attributes={"name": name, "identifiers": {"emails": [f"info@{domain}"]}},
        )
        self.session.add(
            IndustryClassification(
                tenant_id=self.tenant.id,
                source_record_id=record.id,
                status="classified",
                primary_sector=sector,
                rules_version="test",
            )
        )
        return record

    def person(self, external_id: str, email: str, valid_from=T0, **attributes) -> SourceRecord:
        return self.record(
            "person",
            external_id,
            valid_from=valid_from,
            name_normalized=external_id,
            attributes={
                "first_name": "Ana",
                "last_name": external_id,
                "identifiers": {"emails": [email]},
                **attributes,
            },
        )

    def link(self) -> AffiliationReport:
        with bound_to_tenant(self.session, self.tenant.id):
            report = link_people(self.session, self.tenant.id)
            self.session.commit()
        return report

    def affiliations(self) -> list[Affiliation]:
        return self.session.scalars(
            select(Affiliation).where(Affiliation.tenant_id == self.tenant.id)
        ).all()


@pytest.fixture
def net(session):
    return Network(session)


@pytest.fixture
def api(session):
    app.dependency_overrides[database] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_a_work_email_links_the_person_and_keeps_its_evidence(net):
    # Arrange
    acme = net.organization("o1", "Acme", "acme.ca")
    ana = net.person("p1", "ana@acme.ca")

    # Act
    report = net.link()

    # Assert
    (row,) = net.affiliations()
    assert (row.person_record_id, row.organization_record_id) == (ana.id, acme.id)
    assert (row.status, row.method, row.confidence) == ("linked", "email_domain", 0.9)
    assert row.evidence[0]["source"] == "ana@acme.ca"
    assert (report.linked, report.added, report.removed) == (1, 1, 0)


def test_linking_again_keeps_rows_that_still_hold(net):
    # Arrange
    net.organization("o1", "Acme", "acme.ca")
    net.person("p1", "ana@acme.ca")
    net.link()
    (before,) = net.affiliations()

    # Act
    report = net.link()

    # Assert
    (after,) = net.affiliations()
    assert (after.id, after.decided_at) == (before.id, before.decided_at)
    assert (report.added, report.removed) == (0, 0)


def test_a_new_person_version_is_checked_again_and_the_old_link_stays_as_history(net, session):
    # Arrange
    net.organization("o1", "Acme", "acme.ca")
    old = net.person("p1", "ana@acme.ca")
    net.link()
    old.valid_to = T1
    session.flush()
    new = net.person("p1", "ana@gmail.com", valid_from=T1)

    # Act
    report = net.link()

    # Assert
    assert [row.person_record_id for row in net.affiliations()] == [old.id]
    assert new.id not in {row.person_record_id for row in net.affiliations()}
    assert (report.linked, report.added) == (0, 0)


def test_a_removed_organization_drops_the_link(net, session):
    # Arrange
    acme = net.organization("o1", "Acme", "acme.ca")
    net.person("p1", "ana@acme.ca")
    net.link()
    acme.valid_to = T1
    session.flush()

    # Act
    report = net.link()

    # Assert
    assert net.affiliations() == []
    assert report.removed == 1


def test_conflicts_are_listed_for_review(net, api):
    # Arrange
    net.organization("o1", "Acme", "acme.ca")
    net.organization("o2", "Boreal Design", "boreal.ca")
    net.person("p1", "ana@acme.ca", job_title="Conseillere, Boreal Design")
    net.link()

    # Act
    path = f"/api/tenants/{net.tenant.id}/affiliations"
    conflicts = api.get(path, params={"status": "conflict"}).json()
    linked = api.get(path).json()

    # Assert
    assert conflicts["total"] == 2
    assert {item["organization"]["label"] for item in conflicts["items"]} == {
        "Acme",
        "Boreal Design",
    }
    assert {item["person"]["label"] for item in conflicts["items"]} == {"Ana p1"}
    assert linked["total"] == 0


def test_a_relation_from_a_file_import_links_several_organizations(net):
    # Arrange
    net.organization("o1", "Acme", "acme.ca")
    net.organization("o2", "Boreal", "boreal.ca")
    ana = net.person("p1", "ana@gmail.com", organization_id="o1")
    bob = net.person("p2", "bob@gmail.com", organization_id="o2")

    # Act
    report = net.link()

    # Assert
    assert {(r.person_record_id, r.method) for r in net.affiliations()} == {
        (ana.id, "relation"),
        (bob.id, "relation"),
    }
    assert report.linked == 2


def test_people_are_placed_under_their_organization_in_the_sector_mix(net, session):
    # Arrange
    first = net.organization("o1", "Acme", "acme.ca", sector="54")
    second = net.organization("o2", "Acme Quebec", "acme.ca", sector="54")
    net.organization("o3", "Boreal", "boreal.ca", sector="72")
    merged = Organization(tenant_id=net.tenant.id, display_name="Acme")
    session.add(merged)
    session.flush()
    for record in (first, second):
        session.add(
            OrgLink(
                tenant_id=net.tenant.id,
                source_record_id=record.id,
                organization_id=merged.id,
                method="domain",
                confidence=1.0,
                decided_by="test",
            )
        )
    net.person("p1", "ana@acme.ca")
    net.person("p2", "bob@acme.ca")
    net.person("p3", "cy@gmail.com")
    net.link()

    # Act
    with bound_to_tenant(session, net.tenant.id):
        people = linked_people(session, net.tenant.id)
        mix = measure_sector_mix(session, net.run, linked_people)

    # Assert
    placed = sorted(str(p.organization_key) for p in people)
    assert placed == sorted([str(merged.id), str(merged.id), "None"])
    assert mix.people_status == "linked"
    by_code = {s["code"]: s for s in mix.sectors}
    assert by_code["54"]["people_share"] == 100
    assert by_code["72"]["people_share"] == 0
    assert len(mix.people_unplaced) == 1

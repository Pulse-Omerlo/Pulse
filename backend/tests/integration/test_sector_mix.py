# AI contribution: 50% or more AI-generated
"""A network's sector mix is computed from its stored organizations and kept per sync run."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.tenancy import bound_to_tenant
from app.models import (
    IndustryClassification,
    Organization,
    OrgLink,
    SectorMixResult,
    Source,
    SourceRecord,
    SyncRun,
    Tenant,
)
from app.processing.regional_baseline import load_baseline
from app.processing.sector_mix import PersonUnit
from app.services.sector_mix import latest_mix, measure_sector_mix, mix_for_run

pytestmark = pytest.mark.integration

MEDIA_ID = "net-mix"


@pytest.fixture(autouse=True)
def targets(tmp_path, monkeypatch):
    monkeypatch.setenv("SECTOR_TARGETS_DIR", str(tmp_path))
    path = tmp_path / f"{MEDIA_ID}.toml"
    path.write_text('gap_points = 10\n[targets]\n"31-33" = 10\n', encoding="utf-8")
    return path


class Network:
    def __init__(self, session, name: str = "Mix network", media_id: str | None = MEDIA_ID):
        self.session = session
        self.tenant = Tenant(name=name, omerlo_media_id=media_id)
        session.add(self.tenant)
        session.flush()
        self.source = Source(tenant_id=self.tenant.id, kind="mock")
        session.add(self.source)
        session.flush()
        self.runs: list[SyncRun] = []
        self.run()

    def run(self) -> SyncRun:
        run = SyncRun(
            tenant_id=self.tenant.id,
            source_id=self.source.id,
            started_at=datetime(2026, 11, 1 + len(self.runs), tzinfo=UTC),
        )
        self.session.add(run)
        self.session.flush()
        self.runs.append(run)
        return run

    def organization(self, external_id: str, sector: str | None) -> SourceRecord:
        record = SourceRecord(
            tenant_id=self.tenant.id,
            source_id=self.source.id,
            external_id=external_id,
            kind="organization",
            first_seen_run=self.runs[0].id,
            last_seen_run=self.runs[0].id,
            attributes={"name": f"Org {external_id}"},
        )
        self.session.add(record)
        self.session.flush()
        self.session.add(
            IndustryClassification(
                tenant_id=self.tenant.id,
                source_record_id=record.id,
                status="classified" if sector else "unclassified",
                primary_sector=sector,
                rules_version="test",
            )
        )
        return record

    def merge(self, name: str, *records: SourceRecord) -> Organization:
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
                    decided_by="test",
                )
            )
        self.session.flush()
        return organization

    def measure(self, run: SyncRun | None = None, people=None) -> SectorMixResult:
        with bound_to_tenant(self.session, self.tenant.id):
            result = measure_sector_mix(self.session, run or self.runs[-1], people)
            self.session.commit()
            return result


def sector(result: SectorMixResult, code: str) -> dict:
    return next(s for s in result.sectors if s["code"] == code)


def test_a_matched_organization_counts_once_with_all_its_records(session):
    # Arrange
    net = Network(session)
    left, right = net.organization("o-1", "72"), net.organization("o-2", "72")
    merged = net.merge("Auberge du Lac", left, right)
    net.organization("o-3", "62")
    net.organization("o-4", None)
    session.commit()

    # Act
    result = net.measure()

    # Assert
    assert (result.organizations, result.classified, result.people_status) == (3, 2, "not_linked")
    food = sector(result, "72")
    assert (food["organization_count"], food["organization_share"]) == (1, 50.0)
    records = sorted([str(left.id), str(right.id)])
    assert food["organizations"] == [
        {"key": str(merged.id), "name": "Auberge du Lac", "records": records}
    ]
    assert food["people"] is None
    assert [u["name"] for u in result.unclassified] == ["Org o-4"]


def test_planted_gaps_are_stored_ranked_with_their_explanation(session):
    # Arrange
    net = Network(session)
    tech = [net.organization(f"t-{i}", "54") for i in range(3)]
    food = [net.organization(f"f-{i}", "72") for i in range(7)]
    session.commit()

    def people(_session, _tenant_id):
        # 3 tech organizations (30%) employ 1 person (10%); 7 restaurants employ 9.
        return [PersonUnit("p-tech", "Ana", str(tech[0].id))] + [
            PersonUnit(f"p-food-{i}", "Bo", str(food[i % 7].id)) for i in range(9)
        ]

    # Act
    result = net.measure(people=people)

    # Assert
    assert result.people_status == "linked"
    assert [(g["code"], g["kind"], g["size"]) for g in result.gaps] == [
        ("54", "too_few_people", 20.0),
        ("72", "too_few_organizations", 20.0),
        ("31-33", "below_target", 10.0),
    ]
    assert result.gaps[0]["explanation"] == (
        "Professional, scientific and technical services makes up 30.0% of the network's "
        "organizations but only 10.0% of its people."
    )
    assert sector(result, "54")["people"] == [
        {"id": "p-tech", "name": "Ana", "organization": str(tech[0].id)}
    ]
    assert result.settings["targets"] == {"31-33": 10.0}


def test_each_run_keeps_its_result_and_measuring_a_run_again_replaces_it(session):
    # Arrange
    net = Network(session)
    net.organization("o-1", "72")
    session.commit()
    first = net.runs[0]
    net.measure(first)
    net.organization("o-2", "62")
    second = net.run()
    session.commit()

    # Act
    net.measure(second)
    net.measure(first)

    # Assert
    with bound_to_tenant(session, net.tenant.id):
        rows = session.scalars(select(SectorMixResult)).all()
        assert len(rows) == 2
        assert mix_for_run(session, net.tenant.id, first.id).organizations == 2
        assert latest_mix(session, net.tenant.id).sync_run_id == second.id


def test_another_networks_organizations_are_not_counted(session):
    # Arrange
    net = Network(session)
    net.organization("o-1", "72")
    other = Network(session, name="Other", media_id="other")
    other.organization("o-1", "62")
    session.commit()

    # Act
    result = net.measure()

    # Assert
    assert result.organizations == 1
    assert [s["code"] for s in result.sectors] == ["31-33", "72"]
    with bound_to_tenant(session, other.tenant.id):
        assert latest_mix(session, other.tenant.id) is None


def test_a_network_with_a_region_stores_regional_shares_and_their_source(session, targets):
    # Arrange
    targets.write_text('region = "Québec"\nregion_margin = 5\n', encoding="utf-8")
    net = Network(session)
    net.organization("o-1", "54")
    session.commit()
    region = load_baseline().region("421")

    # Act
    result = net.measure()

    # Assert
    baseline = result.settings["baseline"]
    assert (baseline["region"], baseline["region_name"]) == ("421", "Québec")
    assert baseline["unclassified_businesses"] == region.unclassified
    assert baseline["attribution"].startswith("Adapted from Statistics Canada")
    shares = {s["code"]: s["region_share"] for s in result.sectors}
    assert set(shares) == set(region.counts)
    assert shares["72"] == round(region.shares()["72"], 1)
    below = {g["code"] for g in result.gaps if g["kind"] == "below_region"}
    assert "54" not in below
    assert below

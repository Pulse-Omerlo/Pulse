# AI contribution: 50% or more AI-generated
"""The sector mix endpoints, after real sync runs of synthetic data against the mock server."""

# The sync fixtures are imported from test_sync_runs; pytest injects them by name.
# ruff: noqa: F811

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.browse import database
from app.main import app
from app.models import IndustryClassification, OrgLink, SourceRecord, Tenant
from synthetic.mutate import DEMO, apply_set
from tests.integration.test_sync_runs import (  # noqa: F401
    SLUG,
    archive,
    client,
    fixtures,
    mock_app,
    source,
    sync,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def planted_target(tmp_path, monkeypatch):
    """A target no synthetic network reaches, so the mix has a known gap."""
    monkeypatch.setenv("SECTOR_TARGETS_DIR", str(tmp_path))
    (tmp_path / f"{SLUG}.toml").write_text('[targets]\n"22" = 25\n', encoding="utf-8")


@pytest.fixture
def api(session):
    app.dependency_overrides[database] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_a_sync_stores_a_mix_whose_figures_add_up(api, session, source, sync):
    # Act
    run = sync(full=True)
    mix = api.get(f"/api/tenants/{source.tenant_id}/sector-mix").json()

    # Assert
    rows = session.execute(
        select(OrgLink.organization_id, SourceRecord.id, IndustryClassification.primary_sector)
        .outerjoin(OrgLink, OrgLink.source_record_id == SourceRecord.id)
        .outerjoin(
            IndustryClassification, IndustryClassification.source_record_id == SourceRecord.id
        )
        .where(SourceRecord.kind == "organization", SourceRecord.valid_to.is_(None))
    ).all()
    # The sync matches duplicates, so a matched organization counts once.
    organizations = {organization or record for organization, record, _ in rows}
    classified = {organization or record for organization, record, sector in rows if sector}
    assert mix["sync_run_id"] == str(run.id)
    assert (mix["organizations"], mix["classified"]) == (len(organizations), len(classified))
    counted = sum(s["organization_count"] for s in mix["sectors"])
    assert counted + len(mix["unclassified"]) == mix["organizations"]
    assert sum(s["organization_share"] for s in mix["sectors"]) == pytest.approx(100, abs=1)
    assert mix["people_status"] == "not_linked"


def test_the_planted_target_is_reported_as_a_gap(api, source, sync):
    # Act
    sync(full=True)
    mix = api.get(f"/api/tenants/{source.tenant_id}/sector-mix").json()

    # Assert
    utilities = next(g for g in mix["gaps"] if g["code"] == "22")
    assert utilities["kind"] == "below_target"
    assert utilities["explanation"].startswith("Utilities makes up ")
    sizes = [g["size"] for g in mix["gaps"]]
    assert sizes == sorted(sizes, reverse=True)


def test_each_run_keeps_its_own_mix(api, source, sync, fixtures):
    # Arrange
    first = sync(full=True)
    apply_set(fixtures, DEMO)
    second = sync()

    # Act
    latest = api.get(f"/api/tenants/{source.tenant_id}/sector-mix").json()
    earlier = api.get(f"/api/tenants/{source.tenant_id}/runs/{first.id}/sector-mix").json()

    # Assert
    assert latest["sync_run_id"] == str(second.id)
    assert earlier["sync_run_id"] == str(first.id)


def test_a_network_never_synced_has_no_mix_yet(api, session):
    # Arrange
    tenant = Tenant(name="New network")
    session.add(tenant)
    session.commit()

    # Act
    response = api.get(f"/api/tenants/{tenant.id}/sector-mix")

    # Assert
    assert response.status_code == 404

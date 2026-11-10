# AI contribution: 50% or more AI-generated
"""Organizations are classified into NAICS sectors and stored with their evidence."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.tenancy import bound_to_tenant
from app.models import IndustryClassification, Source, SourceRecord, SyncRun, Tenant
from app.services.industry import classify_organizations

pytestmark = pytest.mark.integration

MEDIA_ID = "net-test"


@pytest.fixture
def mappings(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRY_MAPPINGS_DIR", str(tmp_path))
    path = tmp_path / f"{MEDIA_ID}.toml"
    path.write_text(
        '[categories."cat-food"]\nsectors = ["72"]\n[categories."cat-news"]\nsectors = []\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def network(session):
    tenant = Tenant(name="Test network", omerlo_media_id=MEDIA_ID)
    session.add(tenant)
    session.flush()
    source = Source(tenant_id=tenant.id, kind="mock")
    session.add(source)
    session.flush()
    run = SyncRun(tenant_id=tenant.id, source_id=source.id)
    session.add(run)
    session.commit()
    return tenant, source, run


def add_organization(session, network, external_id: str, attributes: dict, **fields):
    tenant, source, run = network
    record = SourceRecord(
        tenant_id=tenant.id,
        source_id=source.id,
        external_id=external_id,
        kind="organization",
        first_seen_run=run.id,
        last_seen_run=run.id,
        attributes=attributes,
        **fields,
    )
    session.add(record)
    session.flush()
    return record


def categories(*ids: str) -> dict:
    return {"profile": {"profile_categories": [{"category_id": i} for i in ids]}}


def classification(session, record) -> IndustryClassification | None:
    return session.get(IndustryClassification, record.id)


def test_current_organizations_are_classified_with_their_evidence(session, network, mappings):
    # Arrange
    tenant = network[0]
    by_category = add_organization(session, network, "o-1", {"name": "X", **categories("cat-food")})
    by_keyword = add_organization(session, network, "o-2", {"name": "Clinique du Parc"})
    unknown = add_organization(session, network, "o-3", {"name": "Studio Nord"})
    session.commit()

    # Act
    with bound_to_tenant(session, tenant.id):
        report = classify_organizations(session, tenant.id)
        session.commit()

    # Assert
    assert (report.classified, report.unclassified) == (2, 1)
    food = classification(session, by_category)
    assert (food.status, food.primary_sector) == ("classified", "72")
    assert food.sectors == [
        {
            "code": "72",
            "method": "category",
            "evidence": [{"field": "category", "value": "cat-food"}],
        }
    ]
    assert classification(session, by_keyword).primary_sector == "62"
    studio = classification(session, unknown)
    assert (studio.status, studio.primary_sector) == ("unclassified", None)
    assert studio.reason == "no mapped category and no keyword matched"


def test_categories_missing_from_the_mapping_are_reported(session, network, mappings):
    # Arrange
    tenant = network[0]
    add_organization(session, network, "o-1", {"name": "Y", **categories("cat-news", "cat-x")})
    add_organization(session, network, "o-2", {"name": "Z", **categories("cat-x")})
    session.commit()

    # Act
    with bound_to_tenant(session, tenant.id):
        report = classify_organizations(session, tenant.id)

    # Assert
    assert dict(report.unmapped_categories) == {"cat-x": 2}


def test_records_already_classified_with_the_same_rules_are_skipped(session, network, mappings):
    # Arrange
    tenant = network[0]
    add_organization(session, network, "o-1", {"name": "Hotel Central"})
    session.commit()
    with bound_to_tenant(session, tenant.id):
        classify_organizations(session, tenant.id)
        session.commit()

    # Act
    with bound_to_tenant(session, tenant.id):
        again = classify_organizations(session, tenant.id)

    # Assert
    assert (again.classified, again.unclassified) == (0, 0)


def test_a_new_version_is_classified_again_and_the_old_result_is_kept(session, network, mappings):
    # Arrange
    tenant = network[0]
    old = add_organization(
        session,
        network,
        "o-1",
        {"name": "Restaurant Le Coin"},
        valid_from=datetime(2026, 11, 1, tzinfo=UTC),
    )
    session.commit()
    with bound_to_tenant(session, tenant.id):
        classify_organizations(session, tenant.id)
        session.commit()
    old.valid_to = datetime(2026, 11, 2, tzinfo=UTC)
    session.flush()
    new = add_organization(
        session,
        network,
        "o-1",
        {"name": "Garage Le Coin"},
        valid_from=datetime(2026, 11, 2, tzinfo=UTC),
    )
    session.commit()

    # Act
    with bound_to_tenant(session, tenant.id):
        report = classify_organizations(session, tenant.id)
        session.commit()

    # Assert
    assert report.classified == 1
    assert classification(session, old).primary_sector == "72"
    assert classification(session, new).primary_sector == "81"


def test_changing_the_mapping_classifies_the_network_again(session, network, mappings):
    # Arrange
    tenant = network[0]
    record = add_organization(session, network, "o-1", {"name": "X", **categories("cat-news")})
    session.commit()
    with bound_to_tenant(session, tenant.id):
        classify_organizations(session, tenant.id)
        session.commit()
    mappings.write_text('[categories."cat-news"]\nsectors = ["51"]\n', encoding="utf-8")

    # Act
    with bound_to_tenant(session, tenant.id):
        report = classify_organizations(session, tenant.id)
        session.commit()

    # Assert
    assert report.classified == 1
    assert classification(session, record).primary_sector == "51"


def test_other_tenants_records_are_left_alone(session, network, mappings):
    # Arrange
    tenant = network[0]
    other = Tenant(name="Other network")
    session.add(other)
    session.flush()
    other_source = Source(tenant_id=other.id, kind="mock")
    session.add(other_source)
    session.flush()
    other_run = SyncRun(tenant_id=other.id, source_id=other_source.id)
    session.add(other_run)
    session.flush()
    foreign = add_organization(
        session, (other, other_source, other_run), "o-1", {"name": "Hotel Ailleurs"}
    )
    session.commit()

    # Act
    with bound_to_tenant(session, tenant.id):
        report = classify_organizations(session, tenant.id)
        session.commit()

    # Assert
    assert (report.classified, report.unclassified) == (0, 0)
    assert (
        session.scalars(
            select(IndustryClassification).where(
                IndustryClassification.source_record_id == foreign.id
            )
        ).all()
        == []
    )

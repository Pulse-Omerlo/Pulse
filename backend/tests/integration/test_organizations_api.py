# AI contribution: 50% or more AI-generated
"""The organization endpoints, after a sync of synthetic data and Tier 1 matching."""

# The sync fixtures are imported from test_sync_runs; pytest injects them by name.
# ruff: noqa: F811

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.api.browse import database
from app.core.tenancy import bound_to_tenant
from app.main import app
from app.models import MatchCandidate, Organization, OrgLink, RecordChange, SourceRecord
from app.processing.entity_resolution import match_organizations
from synthetic.mutate import DEMO, apply_set
from tests.integration.test_sync_runs import (  # noqa: F401
    archive,
    client,
    fixtures,
    mock_app,
    source,
    sync,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def api(session):
    app.dependency_overrides[database] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def matched(session, source, sync, fixtures):
    """Two syncs, the second changing some records, then Tier 1 matching."""
    sync(full=True)
    apply_set(fixtures, DEMO)
    sync()
    with bound_to_tenant(session, source.tenant_id):
        match_organizations(session, source.tenant_id)
        session.commit()
    return f"/api/tenants/{source.tenant_id}"


@contextmanager
def counted_queries(session):
    statements = []

    def count(*_args):
        statements.append(1)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", count)


def current_organizations(session) -> int:
    """Matched organizations plus current records that matched none."""
    rows = session.execute(
        select(OrgLink.organization_id, SourceRecord.id)
        .outerjoin(OrgLink, OrgLink.source_record_id == SourceRecord.id)
        .where(SourceRecord.kind == "organization", SourceRecord.valid_to.is_(None))
    ).all()
    return len({organization or record for organization, record in rows})


def a_group(api, base: str) -> dict:
    return api.get(f"{base}/duplicates/groups", params={"page_size": 1}).json()["items"][0]


def test_organizations_are_counted_once_per_match(api, session, matched):
    # Act
    page = api.get(f"{matched}/organizations", params={"page_size": 100}).json()

    # Assert
    assert page["total"] == current_organizations(session)
    merged = [o for o in page["items"] if o["organization_id"]]
    single = [o for o in page["items"] if not o["organization_id"]]
    assert merged
    assert all(o["record"] is None and o["records"] >= 1 for o in merged)
    assert all(o["record"]["external_id"] and o["records"] == 1 for o in single)


@pytest.mark.parametrize(
    ("sort", "key"),
    # Names sort by the database collation, so only these are compared in Python.
    [("records", lambda o: o["records"]), ("last_changed", lambda o: o["last_changed"])],
)
def test_organizations_are_sorted_both_ways(api, matched, sort, key):
    # Act
    up = api.get(f"{matched}/organizations", params={"sort": sort, "page_size": 100}).json()
    down = api.get(
        f"{matched}/organizations", params={"sort": sort, "order": "desc", "page_size": 100}
    ).json()

    # Assert
    values = [key(o) for o in up["items"]]
    assert values == sorted(values)
    reverse = [key(o) for o in down["items"]]
    assert reverse == sorted(reverse, reverse=True)


def test_pages_do_not_overlap_and_add_up_to_the_total(api, matched):
    # Act
    first = api.get(f"{matched}/organizations", params={"page_size": 5}).json()
    second = api.get(f"{matched}/organizations", params={"page": 2, "page_size": 5}).json()

    # Assert
    def keys(page):
        return {o["organization_id"] or o["record"]["version_id"] for o in page["items"]}

    assert len(keys(first)) == len(keys(second)) == 5
    assert not keys(first) & keys(second)
    assert first["total"] == second["total"]


def test_organizations_can_be_searched_by_name(api, matched):
    # Arrange
    name = api.get(f"{matched}/organizations").json()["items"][0]["name"]

    # Act
    found = api.get(f"{matched}/organizations", params={"q": name[:6]}).json()

    # Assert
    assert name in [o["name"] for o in found["items"]]
    assert all(name[:6].lower() in o["name"].lower() for o in found["items"])


def test_an_organization_shows_its_records_links_and_versions(api, session, matched):
    # Arrange
    group = a_group(api, matched)

    # Act
    detail = api.get(f"{matched}/organizations/{group['id']}").json()

    # Assert
    assert detail == group
    assert len(detail["records"]) >= 2
    for linked in detail["records"]:
        assert linked["link"]["method"] in {"domain", "linkedin", "external_id", "name_city"}
        assert linked["link"]["evidence"]
        assert linked["versions"][-1]["id"] == linked["record"]["version_id"]
        assert linked["versions"][-1]["valid_to"] is None


def test_the_timeline_lists_the_changes_of_every_record_newest_first(api, session, matched):
    # Arrange
    group = a_group(api, matched)
    keys = {(r["record"]["source_id"], r["record"]["external_id"]) for r in group["records"]}

    # Act
    timeline = api.get(f"{matched}/organizations/{group['id']}/timeline").json()

    # Assert
    expected = session.scalar(
        select(func.count(RecordChange.id))
        .join(SourceRecord, SourceRecord.id == RecordChange.source_record_id)
        .where(
            func.concat(SourceRecord.source_id, "/", SourceRecord.external_id).in_(
                [f"{s}/{e}" for s, e in keys]
            )
        )
    )
    assert timeline["total"] == expected >= len(keys)
    entries = timeline["items"]
    assert {(e["record"]["source_id"], e["record"]["external_id"]) for e in entries} <= keys
    started = [e["started_at"] for e in entries]
    assert started == sorted(started, reverse=True)


def test_duplicate_candidates_carry_both_records_and_the_matched_fields(api, session, matched):
    # Arrange
    pending = session.scalar(
        select(func.count(MatchCandidate.id)).where(MatchCandidate.status == "pending")
    )

    # Act
    page = api.get(f"{matched}/duplicates/candidates", params={"page_size": 100}).json()

    # Assert
    assert page["total"] == pending
    for candidate in page["items"]:
        assert candidate["left"]["version_id"] != candidate["right"]["version_id"]
        assert candidate["matched_fields"]
    scores = [c["score"] for c in page["items"]]
    assert scores == sorted(scores, reverse=True)


def test_duplicate_groups_hold_two_or_more_records(api, matched):
    # Act
    page = api.get(f"{matched}/duplicates/groups", params={"page_size": 100}).json()

    # Assert
    assert page["total"] == len(page["items"]) > 0
    assert all(len(group["records"]) >= 2 for group in page["items"])


@pytest.mark.parametrize(
    "path",
    [
        "organizations",
        "duplicates/candidates",
        "duplicates/groups",
        "runs",
        "runs/{run}/changes",
        "organizations/{organization}/timeline",
    ],
)
def test_a_bigger_page_runs_no_more_queries(api, session, matched, path):
    # Arrange
    run = api.get(f"{matched}/runs").json()["items"][0]["id"]
    organization = a_group(api, matched)["id"]
    url = f"{matched}/" + path.format(run=run, organization=organization)
    # The first call loads the tenant into the session; later ones read it from memory.
    api.get(url)

    # Act
    with counted_queries(session) as small:
        one = api.get(url, params={"page_size": 1}).json()
    with counted_queries(session) as large:
        many = api.get(url, params={"page_size": 100}).json()

    # Assert
    assert len(many["items"]) >= len(one["items"])
    assert len(large) == len(small)


def test_a_merged_organization_id_shows_the_organization_it_was_merged_into(
    api, session, source, matched
):
    # Arrange
    survivor = a_group(api, matched)
    with bound_to_tenant(session, source.tenant_id):
        merged = Organization(
            tenant_id=source.tenant_id, display_name="Old name", merged_into=survivor["id"]
        )
        session.add(merged)
        session.commit()
        merged_id = merged.id

    # Act
    detail = api.get(f"{matched}/organizations/{merged_id}")
    timeline = api.get(f"{matched}/organizations/{merged_id}/timeline")

    # Assert
    assert detail.json() == api.get(f"{matched}/organizations/{survivor['id']}").json()
    expected = api.get(f"{matched}/organizations/{survivor['id']}/timeline").json()
    assert timeline.json() == expected
    assert expected["total"] > 0


def test_another_organization_id_is_not_found(api, matched):
    # Act
    response = api.get(f"{matched}/organizations/00000000-0000-0000-0000-000000000000")

    # Assert
    assert response.status_code == 404

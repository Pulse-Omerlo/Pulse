# AI contribution: 50% or more AI-generated
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthetic.pipeline.sinks.files import write_files
from synthetic.pipeline.sinks.omerlo import OmerloSink, TenantGuardError
from synthetic.pipeline.target import Target
from synthetic.pipeline.writers.base import PEOPLE
from synthetic.pipeline.writers.fake import FakeWriter
from synthetic.viewer.__main__ import cache_path, cached
from synthetic.viewer.app import SYNC_HEADER, Source, create_app
from synthetic.viewer.data import Dataset, load_fixtures, load_omerlo, read_records, save_records
from tests.synthetic.pipeline.conftest import WORKSPACE_ID

SYNC = {SYNC_HEADER: "1"}


def now(work):
    work()


def empty() -> Dataset:
    return Dataset("files: empty", [], [])


def serve(data: Dataset) -> TestClient:
    return TestClient(create_app(Source(lambda: data, data)))


@pytest.fixture
def fixture_client(plan, tmp_path):
    folder = write_files(plan, tmp_path)
    with serve(load_fixtures(folder)) as client:
        yield client


@pytest.fixture
def written(plan, target):
    """A fake test tenant holding what a pipeline run wrote."""
    writer = FakeWriter((WORKSPACE_ID,))
    OmerloSink(writer, target, sleep=lambda _: None).run(plan)
    return writer


def test_sector_shares_count_each_entity_once_and_mark_planted_gaps(fixture_client, small_mix):
    # Act
    rows = {r["code"]: r for r in fixture_client.get("/api/sectors").json()}

    # Assert
    assert {code: r["organizations"] for code, r in rows.items()} == {"54": 10, "72": 10}
    assert {code: r["people"] for code, r in rows.items()} == {"54": 3, "72": 27}
    assert rows["54"]["planted_gap"] == 0.1
    assert rows["72"]["planted_gap"] is None
    assert rows["54"]["people_share"] == pytest.approx(0.1)


def test_organizations_and_people_can_be_listed_by_sector(fixture_client):
    # Act
    orgs = fixture_client.get("/api/organizations", params={"sector": "54"}).json()
    people = fixture_client.get("/api/people", params={"sector": "54"}).json()

    # Assert
    assert orgs and all(o["sector"] == "54" for o in orgs)
    assert sum(o["employees"] for o in orgs) == len(people)
    assert all(p["employer_name"] for p in people)


def test_people_can_be_listed_by_employer(fixture_client):
    # Arrange
    employer = next(o for o in fixture_client.get("/api/organizations").json() if o["employees"])

    # Act
    people = fixture_client.get("/api/people", params={"employer": employer["external_id"]})

    # Assert
    assert len(people.json()) == employer["employees"]


def test_an_unknown_sector_is_a_404(fixture_client):
    # Act / Assert
    assert fixture_client.get("/api/organizations", params={"sector": "99"}).status_code == 404


def test_duplicates_name_both_records_and_what_differs(fixture_client, plan):
    # Act
    rows = fixture_client.get("/api/duplicates").json()

    # Assert
    planted = len(plan.ground_truth["organization_duplicates"]) + len(
        plan.ground_truth["person_duplicates"]
    )
    assert len(rows) == planted
    assert all(r["first"] and r["second"] and r["changes"] for r in rows)


def test_the_page_and_summary_are_served(fixture_client, plan):
    # Act
    page = fixture_client.get("/")
    summary = fixture_client.get("/api/summary").json()

    # Assert
    assert "Synthetic data viewer" in page.text
    assert summary["organizations"] == len(plan.organizations)
    assert summary["mix"] == "small"


def test_the_check_is_clean_after_a_full_run(written, target, plan):
    # Arrange
    client = serve(load_omerlo(written, target, plan))

    # Act
    check = client.get("/api/check").json()

    # Assert
    assert check["available"] is True
    assert check["missing_organizations"] == check["missing_people"] == []
    assert check["wrong_employer"] == []
    assert check["unplanned"] == []


def test_the_check_finds_missing_records_and_wrong_employers(written, target, plan):
    # Arrange
    people = list(written.records[PEOPLE])
    del written.records[PEOPLE][people[0]]
    written.employers[people[1]] = next(iter(written.records["organizations"]))
    written.employers.pop(people[2])
    client = serve(load_omerlo(written, target, plan))

    # Act
    check = client.get("/api/check").json()

    # Assert
    assert len(check["missing_people"]) == 1
    assert len(check["wrong_employer"]) >= 1
    assert any(w["found"] is None for w in check["wrong_employer"])


def test_records_from_other_runs_are_left_out(written, target, plan):
    # Arrange
    written.add(PEOPLE, WORKSPACE_ID, {"first_name": "Staff", "last_name": "Omerlo"})
    written.add(
        PEOPLE,
        WORKSPACE_ID,
        {"first_name": "Other", "last_name": "Run"},
        [{"key": "pulse_external_id", "value": "syn-other-1-person-0000"}],
    )

    # Act
    data = load_omerlo(written, target, plan)

    # Assert
    assert data.untouched == 2
    assert len(data.people) == len(plan.people)


def test_without_a_plan_there_is_no_check(written, target):
    # Arrange
    client = serve(load_omerlo(written, target, None))

    # Act / Assert
    assert client.get("/api/check").json() == {"available": False}


def test_the_viewer_refuses_a_target_that_is_not_the_test_workspace(written, target, plan):
    # Arrange
    other = Target(**{**target.__dict__, "base_url": "https://ca.omerlo.app/api"})

    # Act / Assert
    with pytest.raises(TenantGuardError, match="production host"):
        load_omerlo(written, other, plan)
    with pytest.raises(TenantGuardError, match="not visible"):
        load_omerlo(FakeWriter(("elsewhere",)), target, plan)


def test_no_response_contains_the_api_key(written, target, plan, monkeypatch):
    # Arrange
    secret = "sk-test-do-not-leak"
    monkeypatch.setenv("OMERLO_API_KEY", secret)
    client = serve(load_omerlo(written, target, plan))
    paths = [
        "/",
        "/api/summary",
        "/api/sectors",
        "/api/organizations",
        "/api/people",
        "/api/duplicates",
        "/api/check",
        "/api/sync",
    ]

    # Act
    bodies = [client.get(path).text for path in paths]

    # Assert
    assert all(secret not in body for body in bodies)


def test_a_sync_reads_the_source_again_and_serves_what_it_found(written, target, plan):
    # Arrange
    source = Source(lambda: load_omerlo(written, target, plan), start=now)
    source.sync()
    client = TestClient(create_app(source))
    del written.records[PEOPLE][next(iter(written.records[PEOPLE]))]

    # Act
    response = client.post("/api/sync", headers=SYNC)

    # Assert
    assert response.status_code == 202
    assert response.json()["syncing"] is False
    assert client.get("/api/summary").json()["people"] == len(plan.people) - 1


def test_until_the_first_read_ends_the_data_is_unavailable():
    # Arrange
    pending = []
    client = TestClient(create_app(Source(empty, start=pending.append)))

    # Act
    started = client.post("/api/sync", headers=SYNC).json()
    data = client.get("/api/summary")

    # Assert
    assert started == {"syncing": True, "error": None, "read_at": None}
    assert data.status_code == 503
    pending.pop()()
    assert client.get("/api/summary").status_code == 200


def test_only_one_read_runs_at_a_time():
    # Arrange
    pending = []
    source = Source(empty, start=pending.append)

    # Act
    first, second = source.sync(), source.sync()

    # Assert
    assert (first, second) == (True, False)
    assert len(pending) == 1


def test_a_failed_sync_keeps_the_last_data_and_says_why(written, target, plan):
    # Arrange
    data = load_omerlo(written, target, plan)

    def refused():
        raise TenantGuardError("workspace x is not visible to this key")

    client = TestClient(create_app(Source(refused, data, start=now)))

    # Act
    status = client.post("/api/sync", headers=SYNC).json()

    # Assert
    assert status["error"] == "workspace x is not visible to this key"
    assert status["read_at"] == data.read_at
    assert client.get("/api/summary").json()["people"] == len(plan.people)


def test_a_sync_without_the_header_is_refused(fixture_client):
    # Act / Assert
    assert fixture_client.post("/api/sync").status_code == 403


def test_saved_records_come_back_with_the_current_plan_truth(written, target, plan, tmp_path):
    # Arrange
    data = load_omerlo(written, target, plan)
    path = tmp_path / "viewer" / "copy.json"
    save_records(replace(data, truth={"old": True}), path)

    # Act
    loaded = read_records(path, plan.ground_truth)

    # Assert
    assert loaded == data


def test_a_broken_saved_copy_is_ignored(target, plan, tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr("synthetic.viewer.__main__.CACHE_DIR", tmp_path)
    path = cache_path(target, plan)
    path.write_text(json.dumps({"source": "omerlo"}), encoding="utf-8")

    # Act / Assert
    assert cached(path, plan) is None
    assert cached(tmp_path / "none.json", plan) is None


def test_each_run_gets_its_own_saved_copy(target, plan):
    # Act / Assert
    assert cache_path(target, plan) != cache_path(target, None)
    assert plan.mix.name in cache_path(target, plan).name

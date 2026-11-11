# AI contribution: 50% or more AI-generated
import pytest
from fastapi.testclient import TestClient

from synthetic.pipeline.sinks.files import write_files
from synthetic.pipeline.sinks.omerlo import OmerloSink, TenantGuardError
from synthetic.pipeline.target import Target
from synthetic.pipeline.writers.base import PEOPLE
from synthetic.pipeline.writers.fake import FakeWriter
from synthetic.viewer.app import create_app
from synthetic.viewer.data import load_fixtures, load_omerlo
from tests.synthetic.pipeline.conftest import WORKSPACE_ID


@pytest.fixture
def fixture_client(plan, tmp_path):
    folder = write_files(plan, tmp_path)
    with TestClient(create_app(load_fixtures(folder))) as client:
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
    client = TestClient(create_app(load_omerlo(written, target, plan)))

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
    client = TestClient(create_app(load_omerlo(written, target, plan)))

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
    client = TestClient(create_app(load_omerlo(written, target, None)))

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
    client = TestClient(create_app(load_omerlo(written, target, plan)))
    paths = [
        "/",
        "/api/summary",
        "/api/sectors",
        "/api/organizations",
        "/api/people",
        "/api/duplicates",
        "/api/check",
    ]

    # Act
    bodies = [client.get(path).text for path in paths]

    # Assert
    assert all(secret not in body for body in bodies)

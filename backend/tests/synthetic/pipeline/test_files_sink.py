# AI contribution: 50% or more AI-generated
import json

from fastapi.testclient import TestClient

from synthetic.fixture_loader import load_fixtures
from synthetic.mock_omerlo import api_key_for, create_app
from synthetic.pipeline.plan import EXTERNAL_ID_KEY
from synthetic.pipeline.sinks.files import write_files


def test_the_mock_server_serves_a_plan_written_as_files(plan, tmp_path):
    # Arrange
    write_files(plan, tmp_path)
    tenants = load_fixtures(tmp_path)
    workspace = plan.workspaces[0]["id"]
    org = plan.organizations[0]

    # Act
    with TestClient(create_app(tenants), base_url="http://mock") as client:
        headers = {"x-api-key": api_key_for(plan.tenant.slug)}
        listed = client.get(
            f"/api/community/v2/workspaces/{workspace}/organizations",
            headers=headers,
            params={"limit": 100},
        ).json()
        metadata = client.get(
            f"/api/community/v2/organizations/{org.record['id']}/metadata", headers=headers
        ).json()

    # Assert
    assert len(listed["data"]) == len(plan.organizations)
    assert {"key": EXTERNAL_ID_KEY, "value": org.external_id} in metadata["data"]


def test_the_ground_truth_file_holds_sectors_employers_and_gaps(plan, tmp_path):
    # Act
    folder = write_files(plan, tmp_path)

    # Assert
    truth = json.loads((folder / "ground_truth.dev_only.json").read_text(encoding="utf-8"))
    assert truth["dev_only"] is True
    assert truth["gaps"] == {"54": 0.1}
    assert len(truth["sectors"]) == len(plan.organizations)
    assert len(truth["employers"]) == len(plan.people)

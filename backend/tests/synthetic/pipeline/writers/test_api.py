# AI contribution: 50% or more AI-generated
import json

import httpx
import pytest
import respx

from app.ingestion.omerlo.client import OmerloAuthError, create_omerlo_client
from app.ingestion.omerlo.settings import OmerloSettings
from synthetic.pipeline.writers.api import ApiWriter
from synthetic.pipeline.writers.base import WriteRejected

BASE = "https://staging.omerlo.test/api"
BLOCK_TYPE = "block-employer"


@pytest.fixture
def writer():
    settings = OmerloSettings(base_url=BASE, api_key="key", retry_attempts=1)
    with create_omerlo_client(settings, sleep=lambda _: None) as client:
        yield ApiWriter(client)


@respx.mock
def test_create_posts_to_the_workspace_and_returns_the_new_id(writer):
    # Arrange
    route = respx.post(f"{BASE}/community/v2/workspaces/ws-1/organizations").mock(
        return_value=httpx.Response(201, json={"data": {"id": "org-9"}})
    )

    # Act
    record_id = writer.create("organizations", "ws-1", {"name": "Bistro Roy"})

    # Assert
    assert record_id == "org-9"
    assert json.loads(route.calls.last.request.content) == {"name": "Bistro Roy"}


@respx.mock
def test_a_refused_write_carries_omerlos_message_and_status(writer):
    # Arrange
    respx.post(f"{BASE}/community/v2/workspaces/ws-1/people").mock(
        return_value=httpx.Response(400, json={"error": "linkedin is invalid"})
    )

    # Act / Assert
    with pytest.raises(WriteRejected) as raised:
        writer.create("people", "ws-1", {"first_name": "A"})
    assert (raised.value.status, raised.value.message) == (400, "linkedin is invalid")


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_error_on_a_write_stops_the_run(writer, status):
    # Arrange
    respx.put(f"{BASE}/community/v2/people/p-1/metadata").mock(
        return_value=httpx.Response(status, json={"error": "Unauthorized access"})
    )

    # Act / Assert
    with pytest.raises(OmerloAuthError):
        writer.set_metadata("people", "p-1", [])


@respx.mock
def test_a_network_error_on_a_write_is_a_rejection(writer):
    # Arrange
    respx.put(f"{BASE}/community/v2/organizations/o-1/metadata").mock(
        side_effect=httpx.ConnectError("down")
    )

    # Act / Assert
    with pytest.raises(WriteRejected, match="network error: ConnectError"):
        writer.set_metadata("organizations", "o-1", [])


@respx.mock
def test_set_metadata_sends_every_entry_in_one_put(writer):
    # Arrange
    route = respx.put(f"{BASE}/community/v2/organizations/o-1/metadata").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    entries = [{"key": "pulse_external_id", "value": "syn-1"}, {"key": "crm_id", "value": "C"}]

    # Act
    writer.set_metadata("organizations", "o-1", entries)

    # Assert
    assert json.loads(route.calls.last.request.content) == entries


@respx.mock
def test_set_employer_creates_the_block_then_points_its_entry_at_the_organization(writer):
    # Arrange
    respx.get(f"{BASE}/community/v2/people/p-1/blocks").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    blocks = respx.put(f"{BASE}/community/v2/people/p-1/blocks").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "blk-1", "block_type_id": BLOCK_TYPE}]}
        )
    )
    entries = respx.put(f"{BASE}/community/v2/people/p-1/blocks/blk-1/entries").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    # Act
    writer.set_employer("p-1", "org-9", BLOCK_TYPE)

    # Assert
    sent_block = json.loads(blocks.calls.last.request.content)[0]
    assert (sent_block["profile_id"], sent_block["block_type_id"]) == ("p-1", BLOCK_TYPE)
    assert json.loads(entries.calls.last.request.content)[0]["relation_id"] == "org-9"


@respx.mock
def test_get_employer_reads_the_relation_of_the_employer_block(writer):
    # Arrange
    respx.get(f"{BASE}/community/v2/people/p-1/blocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "blk-0", "block_type_id": "other"},
                    {"id": "blk-1", "block_type_id": BLOCK_TYPE},
                ]
            },
        )
    )
    respx.get(f"{BASE}/community/v2/people/p-1/blocks/blk-1/entries").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "e", "relation_id": "org-9"}]})
    )

    # Act / Assert
    assert writer.get_employer("p-1", BLOCK_TYPE) == "org-9"


@respx.mock
def test_get_employer_is_none_without_an_employer_block(writer):
    # Arrange
    respx.get(f"{BASE}/community/v2/people/p-1/blocks").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    # Act / Assert
    assert writer.get_employer("p-1", BLOCK_TYPE) is None


@respx.mock
def test_listing_reads_records_and_metadata(writer):
    # Arrange
    respx.get(f"{BASE}/composer/v2/workspaces").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "ws-1"}]})
    )
    respx.get(f"{BASE}/community/v2/workspaces/ws-1/people").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "p-1"}], "meta": {"next": None}})
    )
    respx.get(f"{BASE}/community/v2/people/p-1/metadata").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "k", "value": "v"}]})
    )

    # Act / Assert
    assert writer.list_workspaces() == [{"id": "ws-1"}]
    assert writer.list_records("people", "ws-1") == [{"id": "p-1"}]
    assert writer.get_metadata("people", "p-1") == [{"key": "k", "value": "v"}]

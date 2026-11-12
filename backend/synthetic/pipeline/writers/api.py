# AI contribution: 50% or more AI-generated
"""Writer that calls the backoffice API over HTTP with the API key."""

import uuid
from typing import Any

import httpx

from app.ingestion.omerlo.client import error_message, get_json, raise_for_auth, read_json
from app.ingestion.omerlo.connector import COMMUNITY_LIST, COMMUNITY_METADATA, WORKSPACES
from app.ingestion.omerlo.pagination import DEFAULT_PAGE_SIZE, paginate
from synthetic.pipeline.writers.base import PEOPLE, WriteRejected

PERSON_BLOCKS = f"/community/v2/{PEOPLE}/{{id}}/blocks"
BLOCK_ENTRIES = f"{PERSON_BLOCKS}/{{block_id}}/entries"


def field_errors(errors: Any, path: str = "") -> list[str]:
    """Flatten Omerlo's validation errors: {"metadata": [{"key": ["is invalid"]}]}."""
    if isinstance(errors, dict):
        return [m for k, v in errors.items() for m in field_errors(v, f"{path}.{k}" if path else k)]
    if isinstance(errors, list) and all(isinstance(e, str) for e in errors):
        return [f"{path} {', '.join(errors)}"]
    if isinstance(errors, list):
        return [m for e in errors for m in field_errors(e, path)]
    return [f"{path} {errors}"]


def rejection_message(response: httpx.Response) -> str:
    """The `error` string, or the `errors` object staging sends for validation failures."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("errors"):
        return "; ".join(dict.fromkeys(field_errors(body["errors"])))
    return error_message(response)


class ApiWriter:
    def __init__(self, client: httpx.Client):
        self.client = client

    def list_workspaces(self) -> list[dict[str, Any]]:
        return list(paginate(self.client, WORKSPACES, page_size=DEFAULT_PAGE_SIZE))

    def list_records(self, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        path = COMMUNITY_LIST.format(workspace_id=workspace_id, path=kind)
        return list(paginate(self.client, path, page_size=DEFAULT_PAGE_SIZE))

    def get_metadata(self, kind: str, record_id: str) -> list[dict[str, str]]:
        return get_json(self.client, COMMUNITY_METADATA.format(path=kind, id=record_id))["data"]

    def create(self, kind: str, workspace_id: str, payload: dict[str, Any]) -> str:
        path = COMMUNITY_LIST.format(workspace_id=workspace_id, path=kind)
        return self.send("POST", path, payload)["data"]["id"]

    def set_metadata(self, kind: str, record_id: str, entries: list[dict[str, str]]) -> None:
        self.send("PUT", COMMUNITY_METADATA.format(path=kind, id=record_id), entries)

    def get_employer(self, person_id: str, block_type_id: str) -> str | None:
        block = self.employer_block(person_id, block_type_id)
        if block is None:
            return None
        path = BLOCK_ENTRIES.format(id=person_id, block_id=block["id"])
        entries = get_json(self.client, path)["data"]
        return entries[0].get("relation_id") if entries else None

    def set_employer(self, person_id: str, org_id: str, block_type_id: str) -> None:
        existing = self.employer_block(person_id, block_type_id)
        block_id = existing["id"] if existing else str(uuid.uuid4())
        block = {"id": block_id, "profile_id": person_id, "block_type_id": block_type_id}
        saved = self.send("PUT", PERSON_BLOCKS.format(id=person_id), [block])["data"]
        # The server may assign its own id; take the block of our type from its answer.
        block_id = next(b["id"] for b in saved if b["block_type_id"] == block_type_id)
        entry = {"id": str(uuid.uuid4()), "relation_id": org_id}
        self.send("PUT", BLOCK_ENTRIES.format(id=person_id, block_id=block_id), [entry])

    def employer_block(self, person_id: str, block_type_id: str) -> dict[str, Any] | None:
        blocks = get_json(self.client, PERSON_BLOCKS.format(id=person_id))["data"]
        return next((b for b in blocks if b["block_type_id"] == block_type_id), None)

    def send(self, method: str, path: str, body: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, json=body)
        except httpx.TransportError as error:
            raise WriteRejected(f"network error: {type(error).__name__}") from error
        raise_for_auth(response, path)
        if response.is_error:
            raise WriteRejected(rejection_message(response), response.status_code)
        return read_json(response, path)

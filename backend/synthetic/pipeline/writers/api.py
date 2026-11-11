# AI contribution: 50% or more AI-generated
"""Writer that calls the backoffice API over HTTP with the API key."""

import uuid
from typing import Any

import httpx

from app.ingestion.omerlo.client import (
    OmerloAuthError,
    error_message,
    get_json,
    read_json,
)
from app.ingestion.omerlo.pagination import paginate
from synthetic.pipeline.writers.base import PEOPLE, WriteRejected


class ApiWriter:
    def __init__(self, client: httpx.Client):
        self.client = client

    def list_workspaces(self) -> list[dict[str, Any]]:
        return list(paginate(self.client, "/composer/v2/workspaces"))

    def list_records(self, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        return list(paginate(self.client, f"/community/v2/workspaces/{workspace_id}/{kind}"))

    def get_metadata(self, kind: str, record_id: str) -> list[dict[str, str]]:
        return get_json(self.client, f"/community/v2/{kind}/{record_id}/metadata")["data"]

    def create(self, kind: str, workspace_id: str, payload: dict[str, Any]) -> str:
        path = f"/community/v2/workspaces/{workspace_id}/{kind}"
        return self.send("POST", path, payload)["data"]["id"]

    def set_metadata(self, kind: str, record_id: str, entries: list[dict[str, str]]) -> None:
        self.send("PUT", f"/community/v2/{kind}/{record_id}/metadata", entries)

    def get_employer(self, person_id: str, block_type_id: str) -> str | None:
        block = self.employer_block(person_id, block_type_id)
        if block is None:
            return None
        path = f"/community/v2/{PEOPLE}/{person_id}/blocks/{block['id']}/entries"
        entries = get_json(self.client, path)["data"]
        return entries[0].get("relation_id") if entries else None

    def set_employer(self, person_id: str, org_id: str, block_type_id: str) -> None:
        existing = self.employer_block(person_id, block_type_id)
        block_id = existing["id"] if existing else str(uuid.uuid4())
        block = {"id": block_id, "profile_id": person_id, "block_type_id": block_type_id}
        saved = self.send("PUT", f"/community/v2/{PEOPLE}/{person_id}/blocks", [block])["data"]
        # The server may assign its own id; take the block of our type from its answer.
        block_id = next(b["id"] for b in saved if b["block_type_id"] == block_type_id)
        entry = {"id": str(uuid.uuid4()), "relation_id": org_id}
        path = f"/community/v2/{PEOPLE}/{person_id}/blocks/{block_id}/entries"
        self.send("PUT", path, [entry])

    def employer_block(self, person_id: str, block_type_id: str) -> dict[str, Any] | None:
        blocks = get_json(self.client, f"/community/v2/{PEOPLE}/{person_id}/blocks")["data"]
        return next((b for b in blocks if b["block_type_id"] == block_type_id), None)

    def send(self, method: str, path: str, body: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, json=body)
        except httpx.TransportError as error:
            raise WriteRejected(f"network error: {type(error).__name__}") from error
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise OmerloAuthError(f"{path}: Omerlo rejected the API key (HTTP 401)")
        if response.status_code == httpx.codes.FORBIDDEN:
            message = "the API key lacks the role for this resource (HTTP 403)"
            raise OmerloAuthError(f"{path}: {message}")
        if response.is_error:
            raise WriteRejected(error_message(response), response.status_code)
        return read_json(response, path)

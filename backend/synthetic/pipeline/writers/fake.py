# AI contribution: 50% or more AI-generated
"""In-memory writer for tests: keeps records, logs calls and refuses what it is told to."""

from dataclasses import dataclass
from typing import Any

from app.ingestion.omerlo.client import OmerloAuthError
from synthetic.pipeline.writers.base import KINDS, ORGANIZATIONS, WriteRejected


@dataclass
class Refusal:
    times: int
    message: str
    status: int


def label_of(payload: dict[str, Any]) -> str:
    return payload.get("name") or f"{payload['first_name']} {payload['last_name']}"


class FakeWriter:
    def __init__(self, workspace_ids: tuple[str, ...] = ("ws-test",)):
        self.workspaces = [{"id": w, "name": w} for w in workspace_ids]
        # kind -> record id -> summary (with the workspace it lives in)
        self.records: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in KINDS}
        self.metadata: dict[str, list[dict[str, str]]] = {}
        self.employers: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.refusals: dict[str, Refusal] = {}
        self.auth_error = False

    def refuse(self, label: str, *, times: int = 1, message: str = "refused", status: int = 400):
        """Refuse the next `times` creates of the record with this name."""
        self.refusals[label] = Refusal(times, message, status)

    def add(self, kind: str, workspace_id: str, summary: dict[str, Any], metadata=()) -> str:
        """A record that exists before the run, for example one someone made by hand."""
        record_id = summary.get("id") or f"{kind}-{len(self.records[kind]) + 1}"
        self.records[kind][record_id] = {**summary, "id": record_id, "workspace": workspace_id}
        self.metadata[record_id] = list(metadata)
        return record_id

    def list_workspaces(self) -> list[dict[str, Any]]:
        self.calls.append(("list_workspaces", ""))
        return list(self.workspaces)

    def list_records(self, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_records", kind))
        return [r for r in self.records[kind].values() if r["workspace"] == workspace_id]

    def get_metadata(self, kind: str, record_id: str) -> list[dict[str, str]]:
        self.calls.append(("get_metadata", record_id))
        return list(self.metadata.get(record_id, []))

    def create(self, kind: str, workspace_id: str, payload: dict[str, Any]) -> str:
        label = label_of(payload)
        self.calls.append(("create", f"{kind}:{label}"))
        if self.auth_error:
            raise OmerloAuthError("the API key lacks the role for this resource (HTTP 403)")
        refusal = self.refusals.get(label)
        if refusal and refusal.times > 0:
            refusal.times -= 1
            raise WriteRejected(refusal.message, refusal.status)
        if kind == ORGANIZATIONS:
            summary = {"name": label}
        else:
            summary = {"first_name": payload["first_name"], "last_name": payload["last_name"]}
        return self.add(kind, workspace_id, {**summary, "profile_image_key": None})

    def set_metadata(self, kind: str, record_id: str, entries: list[dict[str, str]]) -> None:
        self.calls.append(("set_metadata", record_id))
        self.metadata[record_id] = list(entries)

    def get_employer(self, person_id: str, block_type_id: str) -> str | None:
        self.calls.append(("get_employer", person_id))
        return self.employers.get(person_id)

    def set_employer(self, person_id: str, org_id: str, block_type_id: str) -> None:
        self.calls.append(("set_employer", person_id))
        self.employers[person_id] = org_id

# AI contribution: 50% or more AI-generated
"""Writer that hands each call to an external command, such as a CLI with the Omerlo MCP.

The command is run once per call with the operation name as its last argument and the
arguments as a JSON object on stdin. It prints `{"result": ...}` on success, or
`{"error": "message", "status": 400}` when Omerlo refuses the call.
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from app.ingestion.omerlo.client import OmerloAuthError
from synthetic.pipeline.writers.base import WriteRejected

AUTH_STATUSES = (401, 403)


class CliWriter:
    def __init__(
        self,
        command: Sequence[str],
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        timeout: float = 120.0,
    ):
        if not command:
            raise ValueError("the CLI writer needs a command")
        self.command = list(command)
        self.run = run
        self.timeout = timeout

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.call("list_workspaces")

    def list_records(self, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        return self.call("list_records", kind=kind, workspace_id=workspace_id)

    def get_metadata(self, kind: str, record_id: str) -> list[dict[str, str]]:
        return self.call("get_metadata", kind=kind, record_id=record_id)

    def create(self, kind: str, workspace_id: str, payload: dict[str, Any]) -> str:
        return self.call("create", kind=kind, workspace_id=workspace_id, payload=payload)

    def set_metadata(self, kind: str, record_id: str, entries: list[dict[str, str]]) -> None:
        self.call("set_metadata", kind=kind, record_id=record_id, entries=entries)

    def get_employer(self, person_id: str, block_type_id: str) -> str | None:
        return self.call("get_employer", person_id=person_id, block_type_id=block_type_id)

    def set_employer(self, person_id: str, org_id: str, block_type_id: str) -> None:
        self.call("set_employer", person_id=person_id, org_id=org_id, block_type_id=block_type_id)

    def call(self, operation: str, **arguments: Any) -> Any:
        try:
            done = self.run(
                [*self.command, operation],
                input=json.dumps(arguments),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise WriteRejected(f"{operation}: command timed out after {self.timeout:g}s") from None
        try:
            body = json.loads(done.stdout)
        except ValueError:
            detail = done.stderr.strip() or f"exit code {done.returncode}"
            raise WriteRejected(f"{operation}: no JSON answer ({detail})") from None
        if "error" in body:
            status = body.get("status")
            if status in AUTH_STATUSES:
                raise OmerloAuthError(f"{operation}: {body['error']} (HTTP {status})")
            raise WriteRejected(str(body["error"]), status)
        return body.get("result")

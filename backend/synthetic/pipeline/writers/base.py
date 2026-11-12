# AI contribution: 50% or more AI-generated
"""What the Omerlo sink needs from a writer, whatever carries the calls."""

from typing import Any, Protocol

ORGANIZATIONS = "organizations"
PEOPLE = "people"
KINDS = (ORGANIZATIONS, PEOPLE)


class WriteRejected(Exception):
    """Omerlo refused one write; the run can go on with the next record."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status

    def __str__(self) -> str:
        return f"{self.status} {self.message}" if self.status else self.message


class OmerloWriter(Protocol):
    def list_workspaces(self) -> list[dict[str, Any]]: ...

    def list_records(self, kind: str, workspace_id: str) -> list[dict[str, Any]]: ...

    def get_metadata(self, kind: str, record_id: str) -> list[dict[str, str]]: ...

    def create(self, kind: str, workspace_id: str, payload: dict[str, Any]) -> str: ...

    def set_metadata(self, kind: str, record_id: str, entries: list[dict[str, str]]) -> None: ...

    def get_employer(self, person_id: str, block_type_id: str) -> str | None: ...

    def set_employer(self, person_id: str, org_id: str, block_type_id: str) -> None: ...

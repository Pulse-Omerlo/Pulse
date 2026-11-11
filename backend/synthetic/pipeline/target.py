# AI contribution: 50% or more AI-generated
"""Where an Omerlo run writes: a test tenant and the ids its records need."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGET = Path(__file__).resolve().parent.parent / "targets" / "omerlo_staging.toml"


@dataclass(frozen=True)
class Target:
    base_url: str
    tenant_id: str
    workspace_id: str
    locales: tuple[str, ...]
    organization_type: str
    person_type: str
    completed_status: str
    employer_block_type: str


def load_target(path: Path = DEFAULT_TARGET) -> Target:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        return Target(
            base_url=data["base_url"].rstrip("/"),
            tenant_id=data["tenant_id"],
            workspace_id=data["workspace_id"],
            locales=tuple(data["locales"]),
            organization_type=data["profile_types"]["organization"],
            person_type=data["profile_types"]["person"],
            completed_status=data["statuses"]["completed"],
            employer_block_type=data["blocks"]["employer"],
        )
    except KeyError as missing:
        raise ValueError(f"{path.name}: missing {missing.args[0]!r}") from None

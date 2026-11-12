# AI contribution: 50% or more AI-generated
"""What a run did: counts per kind and every record Omerlo refused, with the reason."""

from dataclasses import asdict, dataclass, field
from typing import Any

LINKS = "links"


@dataclass
class Counts:
    created: int = 0
    skipped: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class Rejection:
    kind: str
    external_id: str
    reason: str


@dataclass
class RunReport:
    dry_run: bool = False
    counts: dict[str, Counts] = field(
        default_factory=lambda: {k: Counts() for k in ("organizations", "people", LINKS)}
    )
    # Records in the workspace without our external id; never changed.
    untouched: int = 0
    rejections: list[Rejection] = field(default_factory=list)

    def reject(self, kind: str, external_id: str, reason: str) -> None:
        self.counts[kind].rejected += 1
        self.rejections.append(Rejection(kind, external_id, reason))

    def summary(self) -> dict[str, Any]:
        """The counts as flat log fields: `organizations_created`, `links_rejected`, ..."""
        fields: dict[str, Any] = {"dry_run": self.dry_run, "untouched": self.untouched}
        for kind, counts in self.counts.items():
            fields.update({f"{kind}_{name}": value for name, value in asdict(counts).items()})
        return fields

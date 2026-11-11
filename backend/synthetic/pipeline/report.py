# AI contribution: 50% or more AI-generated
"""What a run did: counts per kind and every record Omerlo refused, with the reason."""

from dataclasses import dataclass, field

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

    def format(self) -> str:
        made = "to create" if self.dry_run else "created"
        lines = ["dry run: nothing was written"] if self.dry_run else []
        for kind, counts in self.counts.items():
            lines.append(
                f"{kind:<14} {made} {counts.created:>4}  skipped {counts.skipped:>4}"
                f"  rejected {counts.rejected:>4}"
            )
        lines.append(f"{'untouched':<14} {self.untouched} record(s) without our external id")
        if self.rejections:
            lines.append("rejected:")
            lines.extend(f"  {r.external_id}  {r.reason}" for r in self.rejections)
        return "\n".join(lines)

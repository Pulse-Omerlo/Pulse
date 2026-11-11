# AI contribution: 50% or more AI-generated
"""A network's sector mix and its gaps, computed from organizations and the people in them.

Every figure can be checked by hand: a sector's share of organizations is its organizations
divided by the classified organizations, and its share of people is the people working
in those organizations divided by the people placed in any sector.
"""

import hashlib
import json
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.processing.industry import sectors

DEFAULT_GAP_POINTS = 10.0

GAP_TOO_FEW_ORGANIZATIONS = "too_few_organizations"
GAP_TOO_FEW_PEOPLE = "too_few_people"
GAP_BELOW_TARGET = "below_target"
GAP_KINDS = (GAP_TOO_FEW_ORGANIZATIONS, GAP_TOO_FEW_PEOPLE, GAP_BELOW_TARGET)

PEOPLE_LINKED = "linked"
PEOPLE_NOT_LINKED = "not_linked"
PEOPLE_STATUSES = (PEOPLE_LINKED, PEOPLE_NOT_LINKED)


@dataclass(frozen=True)
class MixSettings:
    # Two shares of one sector further apart than this, in percentage points, are a gap.
    gap_points: float = DEFAULT_GAP_POINTS
    # Sector code -> the share of organizations, in percent, the network wants at least.
    targets: Mapping[str, float] = field(default_factory=dict)

    @property
    def version(self) -> str:
        payload = json.dumps(
            {"gap_points": self.gap_points, "targets": dict(sorted(self.targets.items()))}
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class OrganizationUnit:
    """One organization of the network: a matched organization or an unmatched record."""

    key: str
    name: str
    # Primary sectors of its current record versions; None for an unclassified version.
    record_sectors: Mapping[str, str | None]

    @property
    def sector(self) -> str | None:
        """The primary sector most of its records give, the lower code on a tie."""
        counts = Counter(s for s in self.record_sectors.values() if s is not None)
        if not counts:
            return None
        return min(counts, key=lambda code: (-counts[code], code))


@dataclass(frozen=True)
class PersonUnit:
    id: str
    name: str
    # Key of the organization the person works for, or None when unknown.
    organization_key: str | None


@dataclass(frozen=True)
class SectorShare:
    code: str
    name: str
    organizations: list[OrganizationUnit]
    organization_share: float
    people: list[PersonUnit] | None
    people_share: float | None
    target: float | None


@dataclass(frozen=True)
class Gap:
    code: str
    kind: str
    # Percentage points between the two shares, or between the share and the target.
    size: float
    explanation: str


@dataclass(frozen=True)
class SectorMix:
    sectors: list[SectorShare]
    gaps: list[Gap]
    organizations: int
    unclassified: list[OrganizationUnit]
    people_status: str
    people: int
    # People with no organization, or whose organization has no sector.
    people_unplaced: list[PersonUnit]

    @property
    def classified(self) -> int:
        return self.organizations - len(self.unclassified)


def load_settings(path: Path) -> MixSettings:
    """Read a network's gap threshold and targets; a missing file gives the defaults."""
    if not path.exists():
        return MixSettings()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    known = sectors()
    targets: dict[str, float] = {}
    for code, value in data.get("targets", {}).items():
        if code not in known:
            raise ValueError(f"{path.name}: unknown sector {code!r}")
        if not 0 <= value <= 100:
            raise ValueError(f"{path.name}: target for {code} must be between 0 and 100")
        targets[code] = float(value)
    gap_points = float(data.get("gap_points", DEFAULT_GAP_POINTS))
    if gap_points < 0:
        raise ValueError(f"{path.name}: gap_points must not be negative")
    return MixSettings(gap_points=gap_points, targets=targets)


def percent(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0.0


def compute_mix(
    organizations: Iterable[OrganizationUnit],
    people: Iterable[PersonUnit] | None,
    settings: MixSettings,
) -> SectorMix:
    """Each sector's share of organizations and people, and the gaps ranked by size.

    `people` is None while people are not linked to organizations; the people shares
    and the gaps that need them are then left out.
    """
    names = {code: sector.en for code, sector in sectors().items()}
    units = sorted(organizations, key=lambda unit: unit.key)
    by_sector: dict[str, list[OrganizationUnit]] = {}
    unclassified = []
    for unit in units:
        if unit.sector is None:
            unclassified.append(unit)
        else:
            by_sector.setdefault(unit.sector, []).append(unit)
    classified = len(units) - len(unclassified)

    sector_of = {unit.key: unit.sector for unit in units}
    people_by_sector: dict[str, list[PersonUnit]] = {}
    unplaced: list[PersonUnit] = []
    all_people = sorted(people or [], key=lambda person: person.id)
    for person in all_people:
        code = sector_of.get(person.organization_key) if person.organization_key else None
        if code is None:
            unplaced.append(person)
        else:
            people_by_sector.setdefault(code, []).append(person)
    placed = len(all_people) - len(unplaced)
    linked = people is not None and placed > 0

    shares = [
        SectorShare(
            code=code,
            name=names[code],
            organizations=by_sector.get(code, []),
            organization_share=percent(len(by_sector.get(code, [])), classified),
            people=people_by_sector.get(code, []) if linked else None,
            people_share=(percent(len(people_by_sector.get(code, [])), placed) if linked else None),
            target=settings.targets.get(code),
        )
        for code in sorted(set(by_sector) | set(people_by_sector) | set(settings.targets))
    ]
    gaps = [gap for share in shares for gap in find_gaps(share, settings.gap_points)]
    gaps.sort(key=lambda gap: (-gap.size, gap.code, GAP_KINDS.index(gap.kind)))
    return SectorMix(
        sectors=shares,
        gaps=gaps,
        organizations=len(units),
        unclassified=unclassified,
        people_status=PEOPLE_LINKED if linked else PEOPLE_NOT_LINKED,
        people=len(all_people),
        people_unplaced=unplaced,
    )


def find_gaps(share: SectorShare, gap_points: float) -> list[Gap]:
    gaps = []
    organizations = share.organization_share
    if share.people_share is not None:
        difference = organizations - share.people_share
        if abs(difference) > gap_points:
            more, fewer = "organizations", "people"
            if difference < 0:
                more, fewer = fewer, more
            gaps.append(
                Gap(
                    code=share.code,
                    kind=GAP_TOO_FEW_PEOPLE if difference > 0 else GAP_TOO_FEW_ORGANIZATIONS,
                    size=round(abs(difference), 1),
                    explanation=(
                        f"{share.name} makes up {max(organizations, share.people_share):.1f}% "
                        f"of the network's {more} but only "
                        f"{min(organizations, share.people_share):.1f}% of its {fewer}."
                    ),
                )
            )
    if share.target is not None and organizations < share.target:
        gaps.append(
            Gap(
                code=share.code,
                kind=GAP_BELOW_TARGET,
                size=round(share.target - organizations, 1),
                explanation=(
                    f"{share.name} makes up {organizations:.1f}% of the network's "
                    f"organizations, below its target of {share.target:.1f}%."
                ),
            )
        )
    return gaps

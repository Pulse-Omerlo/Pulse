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
DEFAULT_REGION_MARGIN = 5.0

GAP_TOO_FEW_ORGANIZATIONS = "too_few_organizations"
GAP_TOO_FEW_PEOPLE = "too_few_people"
GAP_BELOW_TARGET = "below_target"
GAP_BELOW_REGION = "below_region"
GAP_KINDS = (GAP_TOO_FEW_ORGANIZATIONS, GAP_TOO_FEW_PEOPLE, GAP_BELOW_TARGET, GAP_BELOW_REGION)

PEOPLE_LINKED = "linked"
PEOPLE_NOT_LINKED = "not_linked"
PEOPLE_STATUSES = (PEOPLE_LINKED, PEOPLE_NOT_LINKED)


@dataclass(frozen=True)
class MixSettings:
    # Two shares of one sector further apart than this, in percentage points, are a gap.
    gap_points: float = DEFAULT_GAP_POINTS
    # Sector code -> the share of organizations, in percent, the network wants at least.
    targets: Mapping[str, float] = field(default_factory=dict)
    # Census metropolitan area code or name the network is compared with, if any.
    region: str | None = None
    # A sector's share below the region's by more than this, in points, is a gap.
    region_margin: float = DEFAULT_REGION_MARGIN

    @property
    def version(self) -> str:
        payload = json.dumps(
            {
                "gap_points": self.gap_points,
                "targets": dict(sorted(self.targets.items())),
                "region": self.region,
                "region_margin": self.region_margin,
            }
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
    # The sector's share of the region's classified employer businesses, when compared.
    region_share: float | None = None


@dataclass(frozen=True)
class RegionShares:
    """The region a network is compared with: its name and each sector's share."""

    name: str
    shares: Mapping[str, float]
    margin: float
    # Shown in each explanation, e.g. "Statistics Canada, June 2026".
    source: str


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
    region = data.get("region")
    if region is not None and (not isinstance(region, str) or not region.strip()):
        raise ValueError(f"{path.name}: region must be a metropolitan area code or name")
    margin = float(data.get("region_margin", DEFAULT_REGION_MARGIN))
    if margin < 0:
        raise ValueError(f"{path.name}: region_margin must not be negative")
    return MixSettings(
        gap_points=gap_points,
        targets=targets,
        region=region.strip() if region else None,
        region_margin=margin,
    )


def percent(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0.0


def compute_mix(
    organizations: Iterable[OrganizationUnit],
    people: Iterable[PersonUnit] | None,
    settings: MixSettings,
    region: RegionShares | None = None,
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
            region_share=region.shares.get(code, 0.0) if region else None,
        )
        for code in sorted(
            set(by_sector)
            | set(people_by_sector)
            | set(settings.targets)
            | set(region.shares if region else ())
        )
    ]
    gaps = [gap for share in shares for gap in find_gaps(share, settings.gap_points, region)]
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


def find_gaps(
    share: SectorShare, gap_points: float, region: RegionShares | None = None
) -> list[Gap]:
    found = (share_gap(share, gap_points), target_gap(share), region_gap(share, region))
    return [gap for gap in found if gap is not None]


def share_gap(share: SectorShare, gap_points: float) -> Gap | None:
    """The sector's shares of organizations and of people, when too far apart."""
    if share.people_share is None:
        return None
    difference = share.organization_share - share.people_share
    if abs(difference) <= gap_points:
        return None
    if difference > 0:
        kind, more, fewer = GAP_TOO_FEW_PEOPLE, "organizations", "people"
    else:
        kind, more, fewer = GAP_TOO_FEW_ORGANIZATIONS, "people", "organizations"
    high, low = sorted((share.organization_share, share.people_share), reverse=True)
    return Gap(
        code=share.code,
        kind=kind,
        size=round(abs(difference), 1),
        explanation=(
            f"{share.name} makes up {high:.1f}% of the network's {more} "
            f"but only {low:.1f}% of its {fewer}."
        ),
    )


def target_gap(share: SectorShare) -> Gap | None:
    """The sector's share of organizations, when below the network's target."""
    if share.target is None or share.organization_share >= share.target:
        return None
    return Gap(
        code=share.code,
        kind=GAP_BELOW_TARGET,
        size=round(share.target - share.organization_share, 1),
        explanation=(
            f"{share.name} makes up {share.organization_share:.1f}% of the network's "
            f"organizations, below its target of {share.target:.1f}%."
        ),
    )


def region_gap(share: SectorShare, region: RegionShares | None) -> Gap | None:
    """The sector's share of organizations, when below the region's by more than the margin."""
    if region is None or share.region_share is None:
        return None
    difference = share.region_share - share.organization_share
    if difference <= region.margin:
        return None
    return Gap(
        code=share.code,
        kind=GAP_BELOW_REGION,
        size=round(difference, 1),
        explanation=(
            f"{share.name} makes up {share.organization_share:.1f}% of the network's "
            f"organizations but {share.region_share:.1f}% of employer businesses in the "
            f"{region.name} area ({region.source})."
        ),
    )

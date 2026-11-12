# AI contribution: 50% or more AI-generated
"""Read a mix file and turn it into record counts per sector."""

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from synthetic.vocabulary import SECTOR_PATTERNS

SHARE_TOLERANCE = 1e-6
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class MixError(ValueError):
    """The mix file cannot be used as written."""


@dataclass(frozen=True)
class Mix:
    name: str
    seed: int
    organizations: int
    people: int
    # Share of organizations per NAICS sector code; the shares sum to 1.
    sectors: Mapping[str, float]
    # Share of people per planted gap sector, instead of following the organizations.
    gaps: Mapping[str, float] = field(default_factory=dict)
    duplicate_org_share: float = 0.12
    duplicate_person_share: float = 0.04
    # None keeps the generator's own mix of French, bilingual and English records.
    locales: tuple[str, ...] | None = None


def load_mix(path: Path) -> Mix:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    duplicates = data.get("duplicates", {})
    locales = data.get("locales")
    mix = Mix(
        name=data.get("name", path.stem.replace("_", "-")),
        seed=data.get("seed", 490),
        organizations=data.get("organizations", 0),
        people=data.get("people", 0),
        sectors={str(code): float(share) for code, share in data.get("sectors", {}).items()},
        gaps={str(code): float(share) for code, share in data.get("gaps", {}).items()},
        duplicate_org_share=float(duplicates.get("organizations", 0.12)),
        duplicate_person_share=float(duplicates.get("people", 0.04)),
        locales=tuple(locales) if locales is not None else None,
    )
    validate(mix)
    return mix


def validate(mix: Mix) -> None:
    if not NAME_PATTERN.match(mix.name):
        raise MixError(f"name {mix.name!r}: use lowercase letters, digits and hyphens")
    if not isinstance(mix.seed, int):
        raise MixError("seed must be an integer")
    if mix.organizations < 1 or mix.people < 0:
        raise MixError("organizations must be at least 1 and people at least 0")
    if not mix.sectors:
        raise MixError("[sectors] lists no sector")
    unknown = sorted(set(mix.sectors) - set(SECTOR_PATTERNS))
    if unknown:
        known = ", ".join(SECTOR_PATTERNS)
        raise MixError(f"no name patterns for sector(s) {', '.join(unknown)}; known: {known}")
    if any(share <= 0 for share in mix.sectors.values()):
        raise MixError("every sector share must be above 0")
    total = sum(mix.sectors.values())
    if abs(total - 1) > SHARE_TOLERANCE:
        raise MixError(f"sector shares sum to {total:g}, not 1")
    stray = sorted(set(mix.gaps) - set(mix.sectors))
    if stray:
        raise MixError(f"gap sector(s) {', '.join(stray)} not listed in [sectors]")
    if any(not 0 <= share <= 1 for share in mix.gaps.values()):
        raise MixError("every gap share must be between 0 and 1")
    if sum(mix.gaps.values()) > 1 + SHARE_TOLERANCE:
        raise MixError("gap shares sum to more than 1")
    for label, share in (
        ("organizations", mix.duplicate_org_share),
        ("people", mix.duplicate_person_share),
    ):
        if not 0 <= share < 1:
            raise MixError(f"duplicate share for {label} must be at least 0 and below 1")
    if mix.locales is not None and not mix.locales:
        raise MixError("locales is empty")
    # Quotas raise their own errors for counts that cannot be placed.
    people_quotas(mix, org_quotas(mix))


def apportion(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """Split `total` by `weights` with the largest remainder method, so the parts add up."""
    weight_sum = sum(weights.values())
    if total == 0 or weight_sum == 0:
        return dict.fromkeys(weights, 0)
    exact = {key: total * weight / weight_sum for key, weight in weights.items()}
    parts = {key: math.floor(value) for key, value in exact.items()}
    left = total - sum(parts.values())
    # Ties go to the key listed first, so the split does not depend on dict order elsewhere.
    order = sorted(weights, key=lambda key: (-(exact[key] - parts[key]), list(weights).index(key)))
    for key in order[:left]:
        parts[key] += 1
    return parts


def org_quotas(mix: Mix) -> dict[str, int]:
    return apportion(mix.organizations, mix.sectors)


def people_quotas(mix: Mix, orgs: Mapping[str, int]) -> dict[str, int]:
    """People per sector: gap sectors take their share, the rest follow the organization counts."""
    quotas = {code: 0 for code in mix.sectors}
    for code, share in mix.gaps.items():
        quotas[code] = round(share * mix.people)
        if quotas[code] and not orgs[code]:
            raise MixError(f"sector {code} has people but no organization to employ them")
    remaining = mix.people - sum(quotas.values())
    if remaining < 0:
        raise MixError("gap shares place more people than the mix has")
    open_sectors = {code: orgs[code] for code in mix.sectors if code not in mix.gaps}
    if remaining and not any(open_sectors.values()):
        raise MixError(f"{remaining} people left over but every sector is a gap or has no org")
    quotas.update(apportion(remaining, open_sectors))
    return quotas

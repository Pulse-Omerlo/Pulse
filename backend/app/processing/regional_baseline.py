# AI contribution: 50% or more AI-generated
"""Employer business counts by sector for Quebec's census metropolitan areas.

The counts come from Statistics Canada's Canadian Business Counts, with employees
(census metropolitan areas and census subdivisions). `app.load_business_counts` finds the
newest release of that table by its title, since table ids change with each release, and
writes the extract this module reads. Shares are over the businesses Statistics Canada
classified, so its unclassified businesses are left out of every regional share.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).with_name("business_counts") / "quebec_cma.json"

TABLE_TITLE = (
    "Canadian Business Counts, with employees, census metropolitan areas and census subdivisions"
)
# Census metropolitan areas entirely in Quebec; Ottawa - Gatineau crosses into Ontario.
QUEBEC_CMA_CODES = ("408", "421", "433", "442", "447", "462")
TOTAL_SIZE = "Total, with employees"
UNCLASSIFIED = "Unclassified"
# The Open Licence's wording for data that was adapted (here, turned into shares).
ATTRIBUTION = (
    "Adapted from Statistics Canada, Table {table}, {title}, released {release}. "
    "This does not constitute an endorsement by Statistics Canada of this product."
)
LICENCE = "Statistics Canada Open Licence"


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    # Sector code -> employer businesses in it.
    counts: Mapping[str, int]
    unclassified: int

    @property
    def classified(self) -> int:
        return sum(self.counts.values())

    def shares(self) -> dict[str, float]:
        total = self.classified
        return {code: 100 * n / total for code, n in self.counts.items()} if total else {}


@dataclass(frozen=True)
class Baseline:
    table: str
    title: str
    release: str
    reference_period: str
    attribution: str
    regions: Mapping[str, Region]

    def region(self, key: str) -> Region:
        """A region by its census metropolitan area code or its name, accents ignored."""
        for region in self.regions.values():
            if key in (region.code, region.name) or fold(key) == fold(region.name):
                return region
        known = ", ".join(f"{r.code} ({r.name})" for r in self.regions.values())
        raise ValueError(f"unknown region {key!r}; known regions: {known}")

    def source(self, region: Region) -> dict[str, Any]:
        """What the dashboard shows next to a regional share."""
        return {
            "table": self.table,
            "title": self.title,
            "release": self.release,
            "reference_period": self.reference_period,
            "attribution": self.attribution,
            "licence": LICENCE,
            "region": region.code,
            "region_name": region.name,
            "classified_businesses": region.classified,
            "unclassified_businesses": region.unclassified,
        }


def fold(text: str) -> str:
    return text.casefold().replace("é", "e").replace("è", "e").strip()


def parse_baseline(data: Mapping[str, Any]) -> Baseline:
    regions = {
        code: Region(
            code=code,
            name=region["name"],
            counts={sector: int(n) for sector, n in region["counts"].items()},
            unclassified=int(region["unclassified"]),
        )
        for code, region in data["regions"].items()
    }
    return Baseline(
        table=data["table"],
        title=data["title"],
        release=data["release"],
        reference_period=data["reference_period"],
        attribution=data["attribution"],
        regions=regions,
    )


@cache
def load_baseline(path: Path = BASELINE_PATH) -> Baseline:
    return parse_baseline(json.loads(path.read_text(encoding="utf-8")))


# ------------------------------------------------- building the extract from the tables


def newest_table(cubes: Iterable[Mapping[str, Any]], title: str = TABLE_TITLE) -> Mapping:
    """The newest table whose English title starts with `title`."""
    matching = [cube for cube in cubes if str(cube.get("cubeTitleEn", "")).startswith(title)]
    if not matching:
        raise ValueError(f"no Statistics Canada table titled {title!r}")
    return max(matching, key=lambda cube: str(cube.get("releaseTime", "")))


def table_id(product_id: int) -> str:
    """33101097 -> 33-10-1097, the id printed on Statistics Canada's pages."""
    digits = f"{product_id:08d}"
    return f"{digits[:2]}-{digits[2:4]}-{digits[4:]}"


@dataclass(frozen=True)
class Request:
    """The members of one table to ask for, and what each coordinate stands for."""

    product_id: int
    # coordinate -> (region code, sector code or UNCLASSIFIED)
    coordinates: Mapping[str, tuple[str, str]]
    names: Mapping[str, str]


def plan_request(
    metadata: Mapping[str, Any], sector_codes: Iterable[str], region_codes=QUEBEC_CMA_CODES
) -> Request:
    """The coordinates of every sector, and of the unclassified, in every region."""
    geography, size, industry = (d["member"] for d in metadata["dimension"])
    regions = {
        m["classificationCode"]: m
        for m in geography
        if m.get("geoLevel") == 503 and m.get("classificationCode") in region_codes
    }
    missing = set(region_codes) - set(regions)
    if missing:
        raise ValueError(f"regions missing from the table: {sorted(missing)}")
    (total,) = (m["memberId"] for m in size if m["memberNameEn"] == TOTAL_SIZE)
    wanted = set(sector_codes)
    industries = {
        m["classificationCode"]: m["memberId"]
        for m in industry
        if m.get("classificationCode") in wanted
    }
    if set(industries) != wanted:
        raise ValueError(f"sectors missing from the table: {sorted(wanted - set(industries))}")
    (unclassified,) = (m["memberId"] for m in industry if m["memberNameEn"] == UNCLASSIFIED)
    industries[UNCLASSIFIED] = unclassified

    coordinates = {}
    for code, region in regions.items():
        for sector, member in industries.items():
            coordinate = ".".join(str(n) for n in (region["memberId"], total, member, 0, 0, 0, 0))
            coordinates[coordinate + ".0.0.0"] = (code, sector)
    return Request(
        product_id=int(metadata["productId"]),
        coordinates=coordinates,
        names={code: region["memberNameFr"] for code, region in regions.items()},
    )


def build_extract(
    metadata: Mapping[str, Any], request: Request, points: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The extract file's content from the table's metadata and its data points."""
    regions: dict[str, dict[str, Any]] = {
        code: {"name": name, "counts": {}, "unclassified": 0}
        for code, name in request.names.items()
    }
    periods = set()
    for point in points:
        code, sector = request.coordinates[point["coordinate"]]
        (latest,) = point["vectorDataPoint"]
        value = int(latest["value"] or 0)
        periods.add(latest["refPerRaw"])
        if sector == UNCLASSIFIED:
            regions[code]["unclassified"] = value
        else:
            regions[code]["counts"][sector] = value
    if len(periods) != 1:
        raise ValueError(f"data points from several periods: {sorted(periods)}")
    table = table_id(request.product_id)
    title = metadata["cubeTitleEn"]
    release = str(metadata["releaseTime"])[:10]
    return {
        "table": table,
        "title": title,
        "release": release,
        "reference_period": periods.pop(),
        "attribution": ATTRIBUTION.format(table=table, title=title, release=release),
        "regions": regions,
    }

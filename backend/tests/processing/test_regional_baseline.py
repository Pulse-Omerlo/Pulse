# AI contribution: 50% or more AI-generated
"""Regional business counts, and a network's sector mix compared with them."""

from pathlib import Path

import pytest

from app.processing.industry import sectors
from app.processing.regional_baseline import (
    BASELINE_PATH,
    TABLE_TITLE,
    UNCLASSIFIED,
    build_extract,
    load_baseline,
    newest_table,
    plan_request,
    table_id,
)
from app.processing.sector_mix import (
    GAP_BELOW_REGION,
    MixSettings,
    OrganizationUnit,
    RegionShares,
    compute_mix,
    load_settings,
)

EXTRACT = Path(__file__).with_name("fixtures") / "business_counts_extract.json"


def unit(key: str, sector: str | None) -> OrganizationUnit:
    return OrganizationUnit(key=key, name=f"Org {key}", record_sectors={f"r-{key}": sector})


def quebec(margin: float = 5) -> RegionShares:
    region = load_baseline(EXTRACT).region("421")
    return RegionShares(
        name=region.name,
        shares=region.shares(),
        margin=margin,
        source="Statistics Canada, June 2026",
    )


def test_regional_shares_leave_out_unclassified_businesses():
    # Act
    region = load_baseline(EXTRACT).region("421")

    # Assert
    assert (region.classified, region.unclassified) == (1000, 90)
    assert region.shares() == {"54": 30.0, "72": 20.0, "31-33": 50.0}


@pytest.mark.parametrize("key", ["421", "Québec", "quebec", " QUÉBEC "])
def test_a_region_is_found_by_its_code_or_its_name(key):
    # Act / Assert
    assert load_baseline(EXTRACT).region(key).code == "421"


def test_an_unknown_region_names_the_known_ones():
    # Act / Assert
    with pytest.raises(ValueError, match=r"421 \(Québec\)"):
        load_baseline(EXTRACT).region("Laval")


def test_the_source_keeps_the_release_attribution_and_unclassified_count():
    # Arrange
    baseline = load_baseline(EXTRACT)

    # Act
    source = baseline.source(baseline.region("421"))

    # Assert
    assert source["release"] == "2026-08-14"
    assert source["attribution"].startswith("Adapted from Statistics Canada")
    assert source["licence"] == "Statistics Canada Open Licence"
    assert source["unclassified_businesses"] == 90


def test_a_sector_far_below_the_region_is_a_gap():
    # Arrange: manufacturing is 0% of the network but 50% of the region.
    organizations = [unit("a", "54"), unit("b", "72"), unit("c", None)]

    # Act
    mix = compute_mix(organizations, None, MixSettings(), quebec())

    # Assert
    by_code = {share.code: share for share in mix.sectors}
    assert by_code["31-33"].region_share == 50.0
    assert by_code["31-33"].organization_share == 0
    gap = next(g for g in mix.gaps if g.kind == GAP_BELOW_REGION)
    assert (gap.code, gap.size) == ("31-33", 50.0)
    assert gap.explanation == (
        "Manufacturing makes up 0.0% of the network's organizations but 50.0% of "
        "employer businesses in the Québec area (Statistics Canada, June 2026)."
    )


def test_a_sector_within_the_margin_is_not_a_gap():
    # Arrange: professional services are 33.3% of the network and 30% of the region,
    # accommodation and food 33.3% and 20%, so only manufacturing is under-represented.
    organizations = [unit("a", "54"), unit("b", "72"), unit("c", "31-33")]

    # Act
    mix = compute_mix(organizations, None, MixSettings(), quebec(margin=20))

    # Assert
    assert [g for g in mix.gaps if g.kind == GAP_BELOW_REGION] == []


def test_without_a_region_nothing_is_compared():
    # Act
    mix = compute_mix([unit("a", "54")], None, MixSettings())

    # Assert
    assert [share.region_share for share in mix.sectors] == [None]


def test_the_region_and_margin_are_read_from_the_network_settings(tmp_path):
    # Arrange
    path = tmp_path / "net.toml"
    path.write_text('region = "Québec"\nregion_margin = 3\n', encoding="utf-8")

    # Act
    settings = load_settings(path)

    # Assert
    assert (settings.region, settings.region_margin) == ("Québec", 3.0)
    assert settings.version != MixSettings().version


@pytest.mark.parametrize("body", ['region = ""', "region = 421", "region_margin = -1"])
def test_a_bad_region_setting_is_refused(tmp_path, body):
    # Arrange
    path = tmp_path / "net.toml"
    path.write_text(body + "\n", encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match=r"net\.toml"):
        load_settings(path)


def test_the_newest_release_is_found_by_title():
    # Arrange
    cubes = [
        {
            "productId": 33101097,
            "cubeTitleEn": f"{TABLE_TITLE}, December 2025",
            "releaseTime": "2026-02-13",
        },
        {
            "productId": 33101176,
            "cubeTitleEn": f"{TABLE_TITLE}, June 2026",
            "releaseTime": "2026-08-14",
        },
        {
            "productId": 33109999,
            "cubeTitleEn": "Canadian Business Counts, without employees",
            "releaseTime": "2026-09-01",
        },
    ]

    # Act / Assert
    assert newest_table(cubes)["productId"] == 33101176
    assert table_id(33101176) == "33-10-1176"
    with pytest.raises(ValueError):
        newest_table(cubes[2:])


def metadata() -> dict:
    return {
        "productId": 33101176,
        "cubeTitleEn": f"{TABLE_TITLE}, June 2026",
        "releaseTime": "2026-08-14T08:30",
        "dimension": [
            {
                "member": [
                    {
                        "memberId": 89,
                        "classificationCode": "421",
                        "geoLevel": 503,
                        "memberNameFr": "Québec",
                    },
                    {
                        "memberId": 111,
                        "classificationCode": "2423027",
                        "geoLevel": 5,
                        "memberNameFr": "Québec",
                    },
                ]
            },
            {"member": [{"memberId": 1, "memberNameEn": "Total, with employees"}]},
            {
                "member": [
                    {"memberId": 2, "classificationCode": "2", "memberNameEn": UNCLASSIFIED},
                    {"memberId": 15, "classificationCode": "54", "memberNameEn": "Professional"},
                    {"memberId": 21, "classificationCode": "72", "memberNameEn": "Food"},
                ]
            },
        ],
    }


def point(coordinate: str, value: float) -> dict:
    return {
        "coordinate": coordinate,
        "vectorDataPoint": [{"value": value, "refPerRaw": "2026-06-30"}],
    }


def test_the_extract_is_built_from_the_table_data_points():
    # Arrange
    request = plan_request(metadata(), ["54", "72"], region_codes=("421",))
    points = [
        point("89.1.15.0.0.0.0.0.0.0", 300),
        point("89.1.21.0.0.0.0.0.0.0", 200),
        point("89.1.2.0.0.0.0.0.0.0", 90),
    ]

    # Act
    extract = build_extract(metadata(), request, points)

    # Assert
    assert set(request.coordinates) == {p["coordinate"] for p in points}
    assert extract["table"] == "33-10-1176"
    assert extract["reference_period"] == "2026-06-30"
    assert extract["regions"] == {
        "421": {"name": "Québec", "counts": {"54": 300, "72": 200}, "unclassified": 90}
    }


def test_a_sector_missing_from_the_table_is_refused():
    # Act / Assert
    with pytest.raises(ValueError, match="81"):
        plan_request(metadata(), ["54", "81"], region_codes=("421",))


def test_the_committed_extract_has_every_sector_of_every_region():
    # Act
    baseline = load_baseline(BASELINE_PATH)

    # Assert
    assert len(baseline.regions) == 6
    assert all(set(r.counts) == set(sectors()) for r in baseline.regions.values())
    assert baseline.attribution.startswith("Adapted from Statistics Canada")

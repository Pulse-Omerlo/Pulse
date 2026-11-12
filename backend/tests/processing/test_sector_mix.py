# AI contribution: 50% or more AI-generated
import pytest

from app.processing.sector_mix import (
    GAP_BELOW_TARGET,
    GAP_TOO_FEW_ORGANIZATIONS,
    GAP_TOO_FEW_PEOPLE,
    PEOPLE_LINKED,
    PEOPLE_NOT_LINKED,
    MixSettings,
    OrganizationUnit,
    PersonUnit,
    compute_mix,
    load_settings,
)


def org(key: str, *sectors: str | None) -> OrganizationUnit:
    return OrganizationUnit(
        key=key, name=key, record_sectors={f"{key}-r{i}": s for i, s in enumerate(sectors)}
    )


def staff(organization_key: str | None, count: int) -> list[PersonUnit]:
    return [
        PersonUnit(id=f"{organization_key}-p{i}", name="", organization_key=organization_key)
        for i in range(count)
    ]


def network() -> tuple[list[OrganizationUnit], list[PersonUnit]]:
    """Ten classified organizations and one unclassified, with 20 people placed.

    Professional services (54): 3 organizations (30%), 2 people (10%).
    Health care (62): 2 organizations (20%), 12 people (60%).
    Food services (72): 5 organizations (50%), 6 people (30%).
    """
    organizations = [
        org("a", "54"),
        org("b", "54"),
        org("c", "54"),
        org("d", "62"),
        org("e", "62"),
        *(org(k, "72") for k in "fghij"),
        org("z", None),
    ]
    people = [
        *staff("a", 2),
        *staff("d", 6),
        *staff("e", 6),
        *staff("f", 6),
        *staff("z", 3),
        *staff(None, 1),
    ]
    return organizations, people


def test_shares_of_organizations_count_only_classified_organizations():
    # Arrange
    organizations, _ = network()

    # Act
    mix = compute_mix(organizations, None, MixSettings())

    # Assert
    shares = {s.code: s.organization_share for s in mix.sectors}
    assert shares == {"54": 30.0, "62": 20.0, "72": 50.0}
    assert (mix.organizations, mix.classified) == (11, 10)
    assert [u.key for u in mix.unclassified] == ["z"]


def test_each_share_keeps_the_organizations_it_was_computed_from():
    # Arrange
    organizations, people = network()

    # Act
    mix = compute_mix(organizations, people, MixSettings())

    # Assert
    health = next(s for s in mix.sectors if s.code == "62")
    assert [u.key for u in health.organizations] == ["d", "e"]
    assert {p.organization_key for p in health.people} == {"d", "e"}
    assert health.name == "Health care and social assistance"


def test_people_shares_count_only_people_placed_in_a_sector():
    # Arrange
    organizations, people = network()

    # Act
    mix = compute_mix(organizations, people, MixSettings())

    # Assert
    assert {s.code: s.people_share for s in mix.sectors} == {"54": 10.0, "62": 60.0, "72": 30.0}
    assert mix.people_status == PEOPLE_LINKED
    assert (mix.people, len(mix.people_unplaced)) == (24, 4)


def test_gaps_are_flagged_with_a_type_size_and_explanation_and_ranked_by_size():
    # Arrange
    organizations, people = network()

    # Act
    mix = compute_mix(organizations, people, MixSettings(gap_points=10))

    # Assert
    assert [(g.code, g.kind, g.size) for g in mix.gaps] == [
        ("62", GAP_TOO_FEW_ORGANIZATIONS, 40.0),
        ("54", GAP_TOO_FEW_PEOPLE, 20.0),
        ("72", GAP_TOO_FEW_PEOPLE, 20.0),
    ]
    assert mix.gaps[1].explanation == (
        "Professional, scientific and technical services makes up 30.0% of the network's "
        "organizations but only 10.0% of its people."
    )


def test_a_difference_equal_to_the_threshold_is_not_a_gap():
    # Arrange
    organizations, people = network()

    # Act
    mix = compute_mix(organizations, people, MixSettings(gap_points=20))

    # Assert
    assert [(g.code, g.kind) for g in mix.gaps] == [("62", GAP_TOO_FEW_ORGANIZATIONS)]


def test_a_share_below_the_networks_target_is_a_gap_even_with_no_organization():
    # Arrange
    organizations, _ = network()
    settings = MixSettings(targets={"54": 35, "31-33": 5, "72": 10})

    # Act
    mix = compute_mix(organizations, None, settings)

    # Assert
    assert [(g.code, g.kind, g.size) for g in mix.gaps] == [
        ("31-33", GAP_BELOW_TARGET, 5.0),
        ("54", GAP_BELOW_TARGET, 5.0),
    ]
    manufacturing = next(s for s in mix.sectors if s.code == "31-33")
    assert (manufacturing.organization_share, manufacturing.organizations) == (0.0, [])
    assert mix.gaps[0].explanation == (
        "Manufacturing makes up 0.0% of the network's organizations, below its target of 5.0%."
    )


def test_without_linked_people_the_people_figures_and_gaps_are_left_out():
    # Arrange
    organizations, _ = network()
    nobody_placed = staff(None, 3)

    # Act
    not_linked = compute_mix(organizations, None, MixSettings(gap_points=0))
    unplaced = compute_mix(organizations, nobody_placed, MixSettings(gap_points=0))

    # Assert
    for mix in (not_linked, unplaced):
        assert mix.people_status == PEOPLE_NOT_LINKED
        assert all(s.people is None and s.people_share is None for s in mix.sectors)
        assert mix.gaps == []


def test_a_matched_organization_counts_once_under_the_sector_most_of_its_records_give():
    # Arrange
    merged = org("m", "72", "71", "72", None)
    tied = org("t", "72", "71")

    # Act
    mix = compute_mix([merged, tied], None, MixSettings())

    # Assert
    assert (merged.sector, tied.sector) == ("72", "71")
    assert {s.code: len(s.organizations) for s in mix.sectors} == {"71": 1, "72": 1}


def test_an_empty_network_has_no_shares_and_no_gaps():
    # Act
    mix = compute_mix([], [], MixSettings())

    # Assert
    assert (mix.sectors, mix.gaps, mix.organizations) == ([], [], 0)


def test_settings_are_read_from_the_networks_file(tmp_path):
    # Arrange
    path = tmp_path / "net.toml"
    path.write_text('gap_points = 15\n[targets]\n"54" = 12.5\n', encoding="utf-8")

    # Act
    settings = load_settings(path)

    # Assert
    assert (settings.gap_points, dict(settings.targets)) == (15.0, {"54": 12.5})
    assert settings.version != MixSettings().version


def test_a_missing_settings_file_gives_the_defaults(tmp_path):
    # Act / Assert
    assert load_settings(tmp_path / "missing.toml") == MixSettings()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('[targets]\n"99" = 5\n', "unknown sector"),
        ('[targets]\n"54" = 120\n', "between 0 and 100"),
        ("gap_points = -1\n", "must not be negative"),
    ],
)
def test_invalid_settings_are_refused(tmp_path, content, message):
    # Arrange
    path = tmp_path / "net.toml"
    path.write_text(content, encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        load_settings(path)

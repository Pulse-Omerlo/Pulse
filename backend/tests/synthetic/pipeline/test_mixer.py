# AI contribution: 50% or more AI-generated
import re
from dataclasses import replace

import pytest

from synthetic.pipeline.mixer import (
    MixError,
    apportion,
    load_mix,
    org_quotas,
    people_quotas,
    validate,
)


def write(tmp_path, text):
    path = tmp_path / "demo_mix.toml"
    path.write_text(text, encoding="utf-8")
    return path


SECTOR_GAPS = """
seed = 490
organizations = 120
people = 300
locales = ["fr"]

[sectors]
"54" = 0.25
"72" = 0.20
"62" = 0.15
"81" = 0.40

[gaps]
"54" = 0.03
"62" = 0.0
"""


def test_quotas_follow_the_sector_shares_and_planted_gaps(tmp_path):
    # Arrange
    mix = load_mix(write(tmp_path, SECTOR_GAPS))

    # Act
    orgs = org_quotas(mix)
    people = people_quotas(mix, orgs)

    # Assert
    assert orgs == {"54": 30, "72": 24, "62": 18, "81": 48}
    assert people == {"54": 9, "72": 97, "62": 0, "81": 194}


def test_a_mix_file_gets_its_name_from_the_file_and_defaults_for_duplicates(tmp_path):
    # Act
    mix = load_mix(write(tmp_path, SECTOR_GAPS))

    # Assert
    assert mix.name == "demo-mix"
    assert (mix.duplicate_org_share, mix.duplicate_person_share) == (0.12, 0.04)
    assert mix.locales == ("fr",)


@pytest.mark.parametrize(
    ("total", "weights", "expected"),
    [
        (10, {"a": 1, "b": 1, "c": 1}, {"a": 4, "b": 3, "c": 3}),
        (7, {"a": 0.5, "b": 0.5}, {"a": 4, "b": 3}),
        (0, {"a": 1}, {"a": 0}),
        (5, {"a": 0, "b": 0}, {"a": 0, "b": 0}),
    ],
)
def test_apportion_always_adds_up_to_the_total(total, weights, expected):
    # Act / Assert
    assert apportion(total, weights) == expected


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"sectors": {"54": 0.5, "72": 0.4}}, "sum to 0.9"),
        ({"sectors": {"54": 0.5, "99": 0.5}}, "no name patterns for sector(s) 99"),
        ({"sectors": {"54": 1.0, "72": 0.0}}, "above 0"),
        ({"gaps": {"62": 0.1}}, "not listed in [sectors]"),
        ({"gaps": {"54": 1.5}}, "between 0 and 1"),
        ({"gaps": {"54": 0.7, "72": 0.6}}, "more than 1"),
        ({"gaps": {"54": 0.4, "72": 0.4}}, "every sector is a gap"),
        ({"organizations": 0}, "at least 1"),
        ({"name": "Bad Name"}, "lowercase"),
        ({"locales": ()}, "locales is empty"),
        ({"duplicate_org_share": 1.0}, "below 1"),
    ],
)
def test_validate_refuses_a_mix_that_cannot_be_built(small_mix, change, message):
    # Arrange
    mix = replace(small_mix, **change)

    # Act / Assert
    with pytest.raises(MixError, match=re.escape(message)):
        validate(mix)


def test_a_gap_sector_with_people_needs_an_organization(small_mix):
    # Arrange
    mix = replace(small_mix, organizations=1, sectors={"54": 0.5, "72": 0.5})

    # Act / Assert
    with pytest.raises(MixError, match="no organization to employ them"):
        people_quotas(mix, {"54": 0, "72": 1})

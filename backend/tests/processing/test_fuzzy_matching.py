# AI contribution: 50% or more AI-generated
"""Tier 2 scoring and decisions, on unsaved records."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.processing import fuzzy_matching as fm
from tests.processing.fuzzy_helpers import CONFIG, EXCLUDED, record


def match_rules(*records, config=CONFIG, known=frozenset()):
    matches = fm.fuzzy_matches(records, EXCLUDED, known, config)
    return [(match.providePair, match.rule) for match in matches]


@pytest.mark.parametrize(
    "left,right,score",
    [
        ("boulangerie tremblay", "boulangerie tremblai", 0.875),
        ("imprimerie belanger", "imprimerie belager", 0.933),
        ("technologies bernier", "bernier technologies", 1.0),
    ],
)
def test_similar_names_score_high(left, right, score):
    # Act / Assert
    assert fm.similarity(left, right, CONFIG) == pytest.approx(score, abs=0.001)


@pytest.mark.parametrize(
    "left,right",
    [
        # One letter apart, but in the only word that tells the two restaurants apart.
        ("restaurant chez marie", "restaurant chez mario"),
        # Close as a whole, but a distinctive word has no counterpart.
        ("cabinet tremblay gagnon", "cabinet tremblay roy"),
        ("garage tremblay", "restaurant tremblay"),
        ("pharmacie tremblay", "pharmacie gagnon"),
        ("boulangerie tremblay", "tremblay"),
    ],
)
def test_near_misses_stay_below_the_threshold(left, right):
    # Act
    score = fm.similarity(left, right, CONFIG)
    # Assert
    assert score < CONFIG.threshold


def test_names_with_different_numbers_score_zero():
    # Act / Assert
    assert fm.similarity("garage 2000", "garage 3000", CONFIG) == 0.0


def test_names_of_generic_words_only_score_zero():
    # Act / Assert
    assert fm.similarity("groupe services", "groupe service", CONFIG) == 0.0


def test_threshold_is_inclusive():
    # Arrange
    left = record(1, "boulangerie tremblay", city="quebec")
    right = record(2, "boulangerie tremblai", city="quebec")
    score = fm.similarity(left.name_normalized, right.name_normalized, CONFIG)
    # Act
    at = match_rules(left, right, config=replace(CONFIG, threshold=score))
    above = match_rules(left, right, config=replace(CONFIG, threshold=score + 0.001))
    # Assert
    assert at == [((left.id, right.id), "fuzzy")]
    assert above == []


@pytest.mark.parametrize(
    "left_fields,right_fields,rule",
    [
        ({"city": "quebec"}, {"city": "quebec"}, "fuzzy"),
        ({"phones": ["+14185550001"]}, {"phones": ["+14185550001"]}, "fuzzy"),
        ({"emails": ["info@tremblay.ca"]}, {"emails": ["ventes@tremblay.ca"]}, "fuzzy"),
        # Nothing besides the name.
        ({}, {}, "fuzzy_name"),
        ({"city": "quebec"}, {}, "fuzzy_name"),
        # A free mail domain says nothing about the organization.
        ({"emails": ["a@gmail.com"]}, {"emails": ["b@gmail.com"]}, "fuzzy_name"),
        # Different phone numbers alone are not a conflict.
        (
            {"city": "quebec", "phones": ["+14185550001"]},
            {"city": "quebec", "phones": ["+14185550002"]},
            "fuzzy",
        ),
        # Same phone, but the cities disagree.
        (
            {"city": "quebec", "phones": ["+14185550001"]},
            {"city": "levis", "phones": ["+14185550001"]},
            "fuzzy_name",
        ),
        (
            {"city": "quebec", "emails": ["info@tremblay.ca"]},
            {"city": "quebec", "emails": ["info@tremblai.com"]},
            "fuzzy_name",
        ),
    ],
)
def test_secondary_signals_decide_between_link_and_review(left_fields, right_fields, rule):
    # Arrange
    left = record(1, "boulangerie tremblay", **left_fields)
    right = record(2, "boulangerie tremblai", **right_fields)
    # Act
    rules = match_rules(left, right)
    # Assert
    assert rules == [((left.id, right.id), rule)]


@pytest.mark.parametrize(
    "fields",
    [
        {"domain": ("tremblay.ca", "tremblai.ca")},
        {"linkedin": ("company/tremblay", "company/tremblai")},
    ],
)
def test_two_stores_with_different_identifiers_in_one_city_are_only_reviewed(fields):
    # Arrange
    ((field, (ours, theirs)),) = fields.items()
    left = record(1, "technologies bernier", city="rimouski", **{field: ours})
    right = record(2, "bernier technologies", city="rimouski", **{field: theirs})
    # Act
    [match] = fm.fuzzy_matches([left, right], EXCLUDED, config=CONFIG)
    # Assert
    assert match.rule == "fuzzy_name" and not match.strong
    assert (field, ours, theirs) in match.sides


def test_person_linkedin_and_generic_domain_are_not_conflicts():
    # Arrange
    left = record(1, "boulangerie tremblay", city="quebec", linkedin="in/a", domain="gmail.com")
    right = record(2, "boulangerie tremblai", city="quebec", linkedin="in/b")
    # Act / Assert
    assert match_rules(left, right) == [((left.id, right.id), "fuzzy")]


def test_generic_names_in_one_city_do_not_match():
    # Arrange
    records = [
        record(1, "restaurant chez marie", city="quebec"),
        record(2, "restaurant chez mario", city="quebec"),
        record(3, "pharmacie tremblay", city="quebec"),
        record(4, "pharmacie gagnon", city="quebec"),
    ]
    # Act / Assert
    assert match_rules(*records) == []


def test_match_keeps_its_score_and_evidence():
    # Arrange
    left = record(1, "boulangerie tremblay", city="quebec")
    right = record(2, "boulangerie tremblai", city="quebec")
    # Act
    [match] = fm.fuzzy_matches([right, left], EXCLUDED, config=CONFIG)
    # Assert
    assert match.score == 0.875
    assert match.fields == (("name_similarity", "0.875"), ("city", "quebec"))
    assert match.sides == (("name_normalized", "boulangerie tremblay", "boulangerie tremblai"),)


def test_pairs_tier1_classified_or_with_equal_names_are_skipped():
    # Arrange
    left = record(1, "boulangerie tremblay", city="quebec")
    right = record(2, "boulangerie tremblai", city="quebec")
    same_name = record(3, "boulangerie tremblay")
    # Act
    rules = match_rules(left, right, same_name, known={(left.id, right.id)})
    # Assert
    assert rules == [((right.id, same_name.id), "fuzzy_name")]


def test_other_tenants_closed_versions_and_people_are_never_paired():
    # Arrange
    base = record(1, "boulangerie tremblay", city="quebec")
    others = [
        record(2, "boulangerie tremblai", city="quebec", tenant=UUID(int=200)),
        record(3, "boulangerie tremblai", city="quebec", valid_to=datetime(2026, 1, 1, tzinfo=UTC)),
        record(4, "boulangerie tremblai", city="quebec", kind="person"),
        record(5, None, city="quebec"),
    ]
    # Act / Assert
    assert match_rules(base, *others) == []


def counted_scores(monkeypatch):
    calls = []
    original = fm.score
    monkeypatch.setattr(fm, "score", lambda *args: calls.append(args) or original(*args))
    return calls


def test_block_over_the_cap_is_never_scored_pair_by_pair(monkeypatch):
    # Arrange
    config = replace(CONFIG, max_block_size=50, min_common_count=1000)
    records = [record(n, f"tremblay {n}") for n in range(1, 201)]
    calls = counted_scores(monkeypatch)
    # Act
    matches = fm.fuzzy_matches(records, EXCLUDED, config=config)
    # Assert
    # 200 records in one block would be 19,900 comparisons.
    assert calls == [] and matches == []


def test_records_in_several_shared_blocks_are_scored_once(monkeypatch):
    # Arrange
    records = [record(n, f"tremblay {n}", city="quebec") for n in range(1, 11)]
    calls = counted_scores(monkeypatch)
    # Act
    fm.fuzzy_matches(records, EXCLUDED, config=CONFIG)
    # Assert
    # The city and "trem" blocks hold the same ten records; each pair is scored once.
    assert len(calls) == 45

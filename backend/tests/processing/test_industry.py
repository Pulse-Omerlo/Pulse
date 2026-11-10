# AI contribution: 50% or more AI-generated
import pytest

from app.processing.industry import (
    METHOD_CATEGORY,
    METHOD_KEYWORD,
    REASON_NO_INPUT,
    REASON_NO_MATCH,
    CategoryMapping,
    classify,
    keyword_rules,
    load_category_mapping,
    rules_version,
    sectors,
)


def mapping(**sectors_by_category: list[str]) -> CategoryMapping:
    return CategoryMapping(
        sectors_by_category={key: tuple(codes) for key, codes in sectors_by_category.items()},
        version="test",
    )


def test_the_reference_data_has_the_twenty_naics_sectors_in_both_languages():
    # Act
    loaded = sectors()

    # Assert
    assert len(loaded) == 20
    assert loaded["72"].en == "Accommodation and food services"
    assert loaded["72"].fr == "Services d'hébergement et de restauration"
    assert {"31-33", "44-45", "48-49"} <= loaded.keys()


def test_every_keyword_rule_points_to_a_known_sector():
    # Act / Assert
    assert keyword_rules().keys() <= sectors().keys()


def test_mapped_categories_decide_the_sectors_before_any_keyword():
    # Arrange
    categories = mapping(tech=["54", "51"])

    # Act
    result = classify(
        name="Restaurant Chez Marie",
        description=None,
        category_ids=["tech"],
        mapping=categories,
    )

    # Assert
    assert [match.code for match in result.sectors] == ["54", "51"]
    assert result.primary == "54"
    assert all(match.method == METHOD_CATEGORY for match in result.sectors)
    assert result.sectors[0].evidence == ({"field": "category", "value": "tech"},)


def test_the_sector_most_categories_point_to_is_primary():
    # Arrange
    categories = mapping(food=["72"], tourism=["71", "72"])

    # Act
    result = classify(
        name=None, description=None, category_ids=["tourism", "food"], mapping=categories
    )

    # Assert
    assert result.primary == "72"
    assert [match.code for match in result.sectors] == ["72", "71"]


def test_keywords_classify_an_organization_whose_categories_map_to_no_sector():
    # Arrange
    categories = mapping(news=[])

    # Act
    result = classify(
        name="Clinique dentaire du Plateau",
        description=None,
        category_ids=["news"],
        mapping=categories,
    )

    # Assert
    assert result.primary == "62"
    assert result.sectors[0].method == METHOD_KEYWORD
    assert {"field": "name", "value": "clinique"} in result.sectors[0].evidence
    assert result.unmapped_categories == ()


def test_keywords_match_without_accents_and_in_the_plural():
    # Act
    result = classify(
        name="Les Hôtels du Vieux-Québec",
        description=None,
        category_ids=[],
        mapping=mapping(),
    )

    # Assert
    assert result.primary == "72"


def test_keywords_match_whole_words_only():
    # Act
    result = classify(
        name="Minerva Studio",
        description=None,
        category_ids=[],
        mapping=mapping(),
    )

    # Assert
    assert result.primary is None
    assert result.reason == REASON_NO_MATCH


def test_a_keyword_in_the_name_outweighs_one_in_the_description():
    # Act
    result = classify(
        name="Auberge du Lac",
        description="Located near the golf course",
        category_ids=[],
        mapping=mapping(),
    )

    # Assert
    assert [match.code for match in result.sectors] == ["72", "71"]
    assert result.primary == "72"


def test_conflicting_keywords_keep_every_sector_and_break_ties_by_code():
    # Act
    result = classify(
        name="Musee et restaurant",
        description=None,
        category_ids=[],
        mapping=mapping(),
    )

    # Assert
    assert [match.code for match in result.sectors] == ["71", "72"]
    assert result.primary == "71"


def test_an_organization_with_nothing_to_go_on_is_unclassified_with_the_reason():
    # Act
    result = classify(name=None, description="  ", category_ids=[], mapping=mapping())

    # Assert
    assert not result.classified
    assert result.sectors == ()
    assert result.reason == REASON_NO_INPUT


def test_categories_missing_from_the_mapping_are_reported_as_unmapped():
    # Arrange
    categories = mapping(food=["72"], news=[])

    # Act
    result = classify(
        name=None,
        description=None,
        category_ids=["food", "news", "unknown", "unknown"],
        mapping=categories,
    )

    # Assert
    assert result.primary == "72"
    assert result.unmapped_categories == ("unknown",)


def test_a_mapping_file_is_read_by_category_id(tmp_path):
    # Arrange
    path = tmp_path / "network.toml"
    path.write_text(
        '[categories."abc"]\nname = "Gastronomie"\nsectors = ["72"]\n'
        '[categories."def"]\nname = "Actualités"\nsectors = []\n',
        encoding="utf-8",
    )

    # Act
    loaded = load_category_mapping(path)

    # Assert
    assert loaded.sectors_by_category == {"abc": ("72",), "def": ()}


def test_a_missing_mapping_file_maps_nothing(tmp_path):
    # Act
    loaded = load_category_mapping(tmp_path / "missing.toml")

    # Assert
    assert loaded.sectors_by_category == {}


def test_a_mapping_to_an_unknown_sector_is_refused(tmp_path):
    # Arrange
    path = tmp_path / "network.toml"
    path.write_text('[categories."abc"]\nsectors = ["99"]\n', encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match="abc"):
        load_category_mapping(path)


def test_the_rules_version_changes_with_the_network_mapping():
    # Act
    first = rules_version(CategoryMapping(version="a"))
    second = rules_version(CategoryMapping(version="b"))

    # Assert
    assert first != second
    assert first == rules_version(CategoryMapping(version="a"))

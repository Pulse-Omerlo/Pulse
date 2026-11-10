# AI contribution: 50% or more AI-generated
from app.models import SourceRecord
from app.services.industry import classification_inputs


def organization(attributes: dict, name_normalized: str | None = None) -> SourceRecord:
    return SourceRecord(kind="organization", attributes=attributes, name_normalized=name_normalized)


def test_omerlo_records_give_their_name_categories_and_description_text():
    # Arrange
    record = organization(
        {
            "name": "Auberge du Lac",
            "profile": {"profile_categories": [{"id": "pc-1", "category_id": "cat-food"}]},
            "description": [
                {
                    "locale": "fr",
                    "deleted_at": None,
                    "blocks": [
                        {"kind": "richtext", "content_text": "Hébergement en nature"},
                        {"content_text": None, "content_html": "<p>Spa &amp; golf</p>"},
                    ],
                }
            ],
            "locales": [{"locale": "fr", "summary_html": "<b>Gîte</b>", "deleted_at": None}],
        }
    )

    # Act
    name, description, categories = classification_inputs(record)

    # Assert
    assert name == "Auberge du Lac"
    assert categories == ["cat-food"]
    assert description == "Hébergement en nature Spa & golf Gîte"


def test_deleted_descriptions_and_locales_are_ignored():
    # Arrange
    record = organization(
        {
            "name": "Studio",
            "description": [
                {"deleted_at": "2026-01-01", "blocks": [{"content_text": "restaurant"}]}
            ],
            "locales": [{"summary_html": "hotel", "deleted_at": "2026-01-01"}],
        }
    )

    # Act
    _, description, _ = classification_inputs(record)

    # Assert
    assert description is None


def test_file_import_records_fall_back_to_their_name_and_have_no_categories():
    # Arrange
    record = organization({"name": "Clinique Santé Plus", "city": "Québec"})

    # Act / Assert
    assert classification_inputs(record) == ("Clinique Santé Plus", None, [])


def test_a_record_without_a_name_attribute_uses_its_normalized_name():
    # Arrange
    record = organization({"profile": "not a dict"}, name_normalized="garage tremblay")

    # Act / Assert
    assert classification_inputs(record) == ("garage tremblay", None, [])

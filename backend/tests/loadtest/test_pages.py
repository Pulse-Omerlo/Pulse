# AI contribution: 50% or more AI-generated
import pytest

from loadtest.pages import organization_ids, page_count, tenant_id_for_slug

TENANTS = [
    {"id": "11111111-1111-1111-1111-111111111111", "name": "Capitale", "slug": "demo-capitale"},
    {"id": "22222222-2222-2222-2222-222222222222", "name": "Estrie", "slug": "demo-estrie"},
]


def test_finds_the_tenant_by_its_slug():
    # Act / Assert
    assert tenant_id_for_slug(TENANTS, "demo-estrie") == "22222222-2222-2222-2222-222222222222"


def test_an_unknown_slug_says_to_seed_first():
    # Act / Assert
    with pytest.raises(ValueError, match="run app"):
        tenant_id_for_slug(TENANTS, "nowhere")


def test_organization_ids_skips_records_that_matched_nothing():
    # Arrange
    page = {
        "items": [
            {"organization_id": "a", "record": None},
            {"organization_id": None, "record": {"external_id": "x"}},
            {"organization_id": "b", "record": None},
        ]
    }

    # Act / Assert
    assert organization_ids(page) == ["a", "b"]


def test_organization_ids_of_an_empty_page():
    # Act / Assert
    assert organization_ids({"items": []}) == []


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 1), (1, 1), (50, 1), (51, 2), (5500, 110)],
)
def test_page_count(total, expected):
    # Act / Assert
    assert page_count(total, 50) == expected

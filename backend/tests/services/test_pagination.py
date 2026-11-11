# AI contribution: 50% or more AI-generated
import pytest

from app.services.pagination import MAX_PAGE_SIZE, Paging


def test_the_offset_skips_the_earlier_pages():
    # Act / Assert
    assert Paging(page=3, page_size=20).offset == 40


@pytest.mark.parametrize(("page", "page_size"), [(0, 10), (1, 0), (1, MAX_PAGE_SIZE + 1)])
def test_pages_outside_the_limits_are_refused(page, page_size):
    # Act / Assert
    with pytest.raises(ValueError):
        Paging(page=page, page_size=page_size)

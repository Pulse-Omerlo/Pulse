# AI contribution: 50% or more AI-generated
from typing import Annotated

from fastapi import Depends, Query

from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Paging


def paging(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> Paging:
    return Paging(page=page, page_size=page_size)


PageParams = Annotated[Paging, Depends(paging)]

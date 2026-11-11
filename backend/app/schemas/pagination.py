# AI contribution: 50% or more AI-generated
from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    # Items matching the request across all pages.
    total: int
    page: int
    page_size: int

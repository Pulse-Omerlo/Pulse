# AI contribution: 50% or more AI-generated
"""Server-side pages for list endpoints, with a capped page size."""

from dataclasses import dataclass

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Paging:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page starts at 1")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


FIRST_PAGE = Paging()

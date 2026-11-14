# AI contribution: 50% or more AI-generated
"""Helpers for the load test that read API responses; kept apart so tests don't need Locust."""


def tenant_id_for_slug(tenants: list[dict], slug: str) -> str:
    """The id of the tenant with this Omerlo media slug, from GET /api/tenants."""
    for tenant in tenants:
        if tenant["slug"] == slug:
            return tenant["id"]
    raise ValueError(f"No tenant with slug {slug!r}; run app.seed first")


def organization_ids(page: dict) -> list[str]:
    """Ids of the matched organizations on one page of the organization list.

    A record that matched nothing has no organization id, so it is skipped.
    """
    ids = []
    for item in page["items"]:
        if item["organization_id"] is not None:
            ids.append(item["organization_id"])
    return ids


def page_count(total: int, page_size: int) -> int:
    """How many pages a list of `total` items has, at least one."""
    if total <= 0:
        return 1
    return (total + page_size - 1) // page_size

# AI contribution: 50% or more AI-generated
"""Run Tier 1 matching for one tenant."""

import argparse
from collections.abc import Sequence

from app.core.database import get_session_factory
from app.core.logging import configure_logging
from app.core.tenancy import bound_to_tenant, system_access
from app.processing.entity_resolution import match_organizations
from app.sync import SyncCommandError, find_tenant


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Match current organization records using Tier 1 rules."
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="tenant UUID or Omerlo media slug",
    )
    args = parser.parse_args(argv)
    configure_logging()

    with get_session_factory()() as session:
        try:
            with system_access(session):
                tenant_id = find_tenant(session, args.tenant).id

            with bound_to_tenant(session, tenant_id):
                result = match_organizations(session, tenant_id)
                session.commit()
        except SyncCommandError as error:
            parser.exit(2, f"error: {error}\n")

    print(  # noqa: T201
        f"Created {result.organizations_created} organizations, "
        f"{result.links_created} links, "
        f"{result.candidates_created} candidates."
    )


if __name__ == "__main__":
    main()

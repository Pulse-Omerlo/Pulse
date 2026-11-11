# AI contribution: 50% or more AI-generated
"""Run Tier 1 matching for one tenant, or again for the records of one sync run."""

import argparse
import uuid
from collections.abc import Sequence

from app.core.database import get_session_factory
from app.core.logging import configure_logging, get_logger
from app.core.tenancy import bound_to_tenant, system_access
from app.models import SyncRun
from app.processing.entity_resolution import MatchResult, match_organizations
from app.services.sync_runs import match_after_sync
from app.sync import SyncCommandError, find_tenant

logger = get_logger(__name__)


def log_summary(tenant_id: uuid.UUID, result: MatchResult) -> None:
    logger.info(
        "organization_matching_completed",
        tenant_id=str(tenant_id),
        organizations_created=result.organizations_created,
        organizations_merged=result.organizations_merged,
        links_created=result.links_created,
        candidates_created=result.candidates_created,
    )


def match_tenant(session, tenant: str) -> MatchResult:
    """Match every current organization of the tenant."""
    with system_access(session):
        tenant_id = find_tenant(session, tenant).id
    with bound_to_tenant(session, tenant_id):
        result = match_organizations(session, tenant_id)
        session.commit()
    log_summary(tenant_id, result)
    return result


def match_run(session, run_id: uuid.UUID) -> MatchResult:
    """Match the records of a sync run again, for example after matching failed."""
    with system_access(session):
        run = session.get(SyncRun, run_id)
    if run is None:
        raise SyncCommandError(f"No sync run {run_id}")
    if run.status != "succeeded":
        raise SyncCommandError(f"Sync run {run_id} did not succeed; nothing to match")

    with bound_to_tenant(session, run.tenant_id):
        result = match_after_sync(session, run)
    if result is None:
        raise SyncCommandError(f"Matching failed: {run.counts['matching']['error']}")
    log_summary(run.tenant_id, result)
    return result


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Match current organization records using Tier 1 rules."
    )
    parser.add_argument("--tenant", help="tenant UUID or Omerlo media slug")
    parser.add_argument(
        "--sync-run",
        type=uuid.UUID,
        help="sync run UUID: match the records it created or changed again",
    )
    args = parser.parse_args(argv)
    # Exactly one of the two options must be given.
    if (args.tenant is None) == (args.sync_run is None):
        parser.error("give either --tenant or --sync-run")
    configure_logging()

    with get_session_factory()() as session:
        try:
            if args.sync_run is not None:
                match_run(session, args.sync_run)
            else:
                match_tenant(session, args.tenant)
        except SyncCommandError as error:
            parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()

# AI contribution: 50% or more AI-generated
"""Run one sync of a tenant's source, or of every active source, and print the sync runs.

uv run python -m app.sync --tenant demo-capitale            # incremental
uv run python -m app.sync --tenant demo-capitale --full     # also marks removed records
uv run python -m app.sync --all                             # every active source, for the schedule
"""

import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.core.logging import configure_logging, get_logger
from app.core.monitoring import configure_sentry
from app.core.tenancy import bound_to_tenant, system_access
from app.ingestion.omerlo.client import create_omerlo_client
from app.ingestion.omerlo.connector import OmerloConnector
from app.ingestion.omerlo.settings import OmerloSettings
from app.models import Source, SyncRun, Tenant
from app.seed import MOCK_OMERLO_BASE_URL
from app.services.sync_runs import COUNT_KEYS, OmerloReader, run_sync
from app.storage.settings import create_archive

logger = get_logger(__name__)

# File sources are imported with app.import_file instead.
SYNCED_KINDS = ("omerlo", "mock")


class SyncCommandError(Exception):
    """The command cannot start a sync, e.g. the tenant or source does not exist."""


def find_tenant(session: Session, key: str) -> Tenant:
    """A tenant by id or by Omerlo media slug."""
    try:
        condition = Tenant.id == uuid.UUID(key)
    except ValueError:
        condition = Tenant.omerlo_media_id == key
    tenant = session.scalar(select(Tenant).where(condition))
    if tenant is None:
        raise SyncCommandError(f"No tenant {key!r}")
    return tenant


def find_source(
    session: Session, tenant: Tenant, source_id: str | None, kinds: tuple[str, ...]
) -> Source:
    """The tenant's source of one of `kinds` with that id, or its only one when no id is given."""
    statement = select(Source).where(Source.tenant_id == tenant.id, Source.kind.in_(kinds))
    if source_id is not None:
        try:
            statement = statement.where(Source.id == uuid.UUID(source_id))
        except ValueError:
            raise SyncCommandError(f"Source id {source_id!r} is not a UUID") from None
    sources = session.scalars(statement).all()
    if not sources:
        raise SyncCommandError(f"Tenant {tenant.name!r} has no source {source_id or ''}".strip())
    if len(sources) > 1:
        ids = ", ".join(str(s.id) for s in sources)
        raise SyncCommandError(f"Tenant {tenant.name!r} has several sources, pick one: {ids}")
    return sources[0]


def omerlo_settings(source: Source, tenant: Tenant) -> OmerloSettings:
    """Connection settings: the source's base URL, the key from OMERLO_API_KEY.

    A mock source uses the mock server's key for the tenant, `mock-<slug>`.
    """
    config = source.config or {}
    if source.kind == "mock":
        if not tenant.omerlo_media_id:
            raise SyncCommandError("A mock source needs a tenant with an Omerlo media slug")
        return OmerloSettings(
            base_url=config.get("base_url") or MOCK_OMERLO_BASE_URL,
            api_key=f"mock-{tenant.omerlo_media_id}",
        )
    if source.kind == "omerlo":
        overrides = {"base_url": config["base_url"]} if config.get("base_url") else {}
        return OmerloSettings(**overrides)
    raise SyncCommandError(f"Sources of kind {source.kind!r} cannot be synced yet")


def summary(run: SyncRun) -> str:
    counts = ", ".join(f"{key} {run.counts.get(key, 0)}" for key in COUNT_KEYS)
    line = f"Sync run {run.id} ({run.mode}) {run.status}: {counts}"
    return f"{line}\n{run.error}" if run.error else line


def sync_source(session: Session, tenant: Tenant, source: Source, *, full: bool) -> SyncRun:
    """Run one sync of `source`. The session must already be bound to the source's tenant."""
    settings = omerlo_settings(source, tenant)
    with create_omerlo_client(settings) as client:
        connector = OmerloConnector(client, workspace_ids=source.config.get("workspace_ids"))
        return run_sync(session, source, OmerloReader(connector), create_archive(), full=full)


def sync(session: Session, tenant_key: str, source_id: str | None, *, full: bool) -> SyncRun:
    # Finding the tenant by its slug looks across tenants; everything after is scoped to it.
    with system_access(session):
        tenant = find_tenant(session, tenant_key)
    with bound_to_tenant(session, tenant.id):
        source = find_source(session, tenant, source_id, SYNCED_KINDS)
        return sync_source(session, tenant, source, full=full)


def active_sources(session: Session) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant id, source id) of every source that can be synced, in every tenant.

    A source is skipped when its config has "active": false.
    """
    with system_access(session):
        sources = session.scalars(
            select(Source)
            .where(Source.kind.in_(SYNCED_KINDS))
            .order_by(Source.tenant_id, Source.id)
        ).all()
        result = []
        for source in sources:
            config = source.config or {}
            if config.get("active", True):
                result.append((source.tenant_id, source.id))
    return result


def sync_all(session: Session, *, full: bool) -> bool:
    """Sync every active source of every tenant and return whether all of them succeeded.

    A source that fails is logged and the next one still runs, so one broken source
    does not stop the others.
    """
    all_succeeded = True
    for tenant_id, source_id in active_sources(session):
        with system_access(session):
            tenant = session.get(Tenant, tenant_id)
        with bound_to_tenant(session, tenant_id):
            source = session.get(Source, source_id)
            # Read before syncing: the sync commits, and reloading needs the tenant scope.
            label = f"{tenant.name} (source {source_id})"
            try:
                run = sync_source(session, tenant, source, full=full)
            except Exception as error:
                session.rollback()
                logger.exception("source_sync_failed", source_id=str(source_id))
                print(f"{label}: not synced: {error}")
                all_succeeded = False
                continue
            print(f"{label}: {summary(run)}")
            if run.status != "succeeded":
                all_succeeded = False
    return all_succeeded


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run one sync of a tenant's source, or of every active source."
    )
    parser.add_argument("--tenant", help="tenant id or Omerlo media slug")
    parser.add_argument("--source", help="source id; optional when the tenant has one source")
    parser.add_argument(
        "--all", action="store_true", help="sync every active source of every tenant"
    )
    parser.add_argument(
        "--full", action="store_true", help="fetch everything and mark missing records removed"
    )
    args = parser.parse_args(argv)
    # Exactly one of --tenant and --all.
    if args.all == (args.tenant is not None):
        parser.error("give either --tenant or --all")
    if args.all and args.source is not None:
        parser.error("--source only works with --tenant")
    configure_logging()
    configure_sentry()

    with get_session_factory()() as session:
        if args.all:
            succeeded = sync_all(session, full=args.full)
        else:
            try:
                run = sync(session, args.tenant, args.source, full=args.full)
            except SyncCommandError as error:
                parser.exit(2, f"error: {error}\n")
            print(summary(run))
            succeeded = run.status == "succeeded"
    if not succeeded:
        sys.exit(1)


if __name__ == "__main__":
    main()

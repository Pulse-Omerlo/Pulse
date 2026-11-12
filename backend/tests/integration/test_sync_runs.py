# AI contribution: 50% or more AI-generated
"""Sync runs against the mock Omerlo server and a real database."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import match as match_command
from app import sync as sync_command
from app.ingestion.connector import parse_timestamp
from app.ingestion.omerlo.connector import COMMUNITY_DETAIL, OmerloConnector
from app.match import match_run
from app.models import (
    IndustryClassification,
    MatchCandidate,
    Organization,
    OrgLink,
    RecordChange,
    Source,
    SourceRecord,
    SyncRun,
    Tenant,
)
from app.models import RawRecord as RawRecordRow
from app.processing.entity_resolution import MatchResult, match_organizations
from app.seed import seed
from app.services import sync_runs
from app.services.sync_lock import source_sync_lock
from app.services.sync_runs import OmerloReader, run_sync
from app.storage.local import LocalArchive
from synthetic.fixture_loader import load_fixtures
from synthetic.mock_omerlo import api_key_for, create_app
from synthetic.mutate import DEMO, apply_set
from synthetic.omerlo_fixtures import OmerloFixtureGenerator, iso, write_fixtures
from tests.fixture_diff import assert_fields_correspond, expected
from tests.synthetic.conftest import SMALL

pytestmark = pytest.mark.integration

SEED = 5
SLUG = "demo-capitale"
# Fixture kind -> entity kind, for the records that become source records.
PROFILE_FILES = {
    "organizations": "organization",
    "people": "person",
    "projects": "project",
    "events": "event",
}
ENTITY_FILES = {**PROFILE_FILES, "event_sessions": "event_session"}


class Clock:
    """Each call is one minute after the previous one, so runs never share a start."""

    def __init__(self):
        self.moment = datetime(2026, 11, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(minutes=1)
        return self.moment


@pytest.fixture
def fixtures(tmp_path):
    out = tmp_path / "fixtures"
    write_fixtures(OmerloFixtureGenerator(SEED, SMALL).generate(), out, SEED)
    return out


@pytest.fixture
def mock_app(fixtures):
    return create_app(load_fixtures(fixtures), fixtures)


def mock_client(app, slug: str = SLUG) -> TestClient:
    return TestClient(app, base_url="http://mock/api", headers={"x-api-key": api_key_for(slug)})


@pytest.fixture
def client(mock_app):
    with mock_client(mock_app) as c:
        yield c


@pytest.fixture
def source(session):
    tenant = Tenant(name="Média démo Capitale", omerlo_media_id=SLUG)
    session.add(tenant)
    session.flush()
    source = Source(tenant_id=tenant.id, kind="mock")
    session.add(source)
    session.commit()
    return source


@pytest.fixture
def archive(tmp_path):
    return LocalArchive(tmp_path / "archive")


@pytest.fixture
def sync(session, source, client, archive):
    clock = Clock()

    def run(full: bool = False, **options) -> SyncRun:
        reader = OmerloReader(OmerloConnector(options.pop("client", client)))
        return run_sync(session, source, reader, archive, full=full, now=clock, **options)

    return run


def tenant_fixtures(fixtures):
    return next(t for t in load_fixtures(fixtures) if t.tenant.slug == SLUG)


def version_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(SourceRecord))


def current_ids(session: Session) -> set[str]:
    statement = select(SourceRecord.external_id).where(SourceRecord.valid_to.is_(None))
    return set(session.scalars(statement))


def ids_of(entries) -> set[str]:
    """Ids of the diff entries that are source records."""
    return {entry["id"] for entry in entries if entry["kind"] in ENTITY_FILES}


def apply_demo(fixtures) -> tuple[dict, set[str]]:
    """Apply the demo set: its diff, and the sessions it dropped from events that remain.

    Those are the only removals an incremental sync can see, through the event re-read.
    """
    sessions = {s["id"]: s for s in tenant_fixtures(fixtures).resources["event_sessions"]}
    diff = apply_set(fixtures, DEMO)["tenants"][SLUG]
    deleted_events = {e["id"] for e in diff["removed"] if e["kind"] == "events"}
    dropped = {
        entry["id"]
        for entry in diff["removed"]
        if entry["kind"] == "event_sessions"
        and sessions[entry["id"]]["event_id"] not in deleted_events
    }
    return diff, dropped


def test_first_full_sync_stores_every_record_and_sets_the_watermarks(
    session, source, sync, fixtures
):
    # Arrange
    data = tenant_fixtures(fixtures)
    expected_ids = {r["id"] for kind in ENTITY_FILES for r in data.resources[kind]}

    # Act
    run = sync(full=True)

    # Assert
    assert (run.status, run.mode, run.error) == ("succeeded", "full", None)
    assert run.finished_at > run.started_at
    assert run.counts["new"] == len(expected_ids)
    assert {k: run.counts[k] for k in ("changed", "unchanged", "removed", "failed")} == {
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
        "failed": 0,
    }
    assert current_ids(session) == expected_ids
    raw_rows = session.scalar(select(func.count()).where(RawRecordRow.sync_run_id == run.id))
    assert raw_rows == run.counts["fetched"]
    session.refresh(source)
    for workspace in data.resources["workspaces"]:
        for kind, entity_kind in PROFILE_FILES.items():
            in_workspace = [
                parse_timestamp(r["updated_at"])
                for r in data.resources[kind]
                if r["profile"]["workspace_id"] == workspace["id"]
            ]
            stored = source.workspace_watermarks[workspace["id"]][entity_kind]
            assert parse_timestamp(stored) == max(in_workspace)


def test_incremental_sync_after_the_demo_mutation_stores_what_changed(
    session, source, sync, fixtures
):
    # Arrange
    first = sync(full=True)
    session.refresh(source)
    before = dict(source.workspace_watermarks)
    diff, dropped = apply_demo(fixtures)

    # Act
    run = sync()

    # Assert
    assert (run.status, run.mode) == ("succeeded", "incremental")
    assert run.counts["fetched"] < first.counts["fetched"]
    assert run.counts["new"] == len(ids_of(diff["added"]))
    assert run.counts["changed"] == len(ids_of(diff["modified"]))
    assert run.counts["removed"] == len(dropped) == 1
    new_versions = select(SourceRecord.external_id).where(SourceRecord.first_seen_run == run.id)
    assert set(session.scalars(new_versions)) == ids_of(diff["added"]) | ids_of(diff["modified"])
    for external_id in ids_of(diff["modified"]):
        versions = session.scalars(
            select(SourceRecord).where(SourceRecord.external_id == external_id)
        ).all()
        assert sorted(v.valid_to is None for v in versions) == [False, True]
    # Records deleted outright are no longer listed, so they stay current until a full sync.
    assert ids_of(diff["removed"]) - dropped <= current_ids(session)
    assert not dropped & current_ids(session)
    session.refresh(source)
    assert source.workspace_watermarks != before


def test_full_sync_after_the_demo_mutation_marks_deleted_records_removed(session, sync, fixtures):
    # Arrange
    sync(full=True)
    diff = apply_set(fixtures, DEMO)["tenants"][SLUG]

    # Act
    run = sync(full=True)

    # Assert
    removed = ids_of(diff["removed"])
    assert run.counts["removed"] == len(removed)
    closed = select(SourceRecord.external_id).where(SourceRecord.removed_run == run.id)
    assert set(session.scalars(closed)) == removed
    assert not removed & current_ids(session)


@pytest.mark.parametrize("full", [False, True])
def test_running_the_same_sync_twice_creates_no_new_versions(session, sync, full):
    # Arrange
    sync(full=True)
    versions = version_count(session)

    # Act
    run = sync(full=full)

    # Assert
    assert run.status == "succeeded"
    assert (run.counts["new"], run.counts["changed"], run.counts["removed"]) == (0, 0, 0)
    assert version_count(session) == versions
    assert recorded_changes(session, run) == {}
    if full:
        assert run.counts["unchanged"] == versions


def test_cleaned_resync_then_mutation_still_opens_versions(session, sync, fixtures):
    first = sync(full=True)
    versions = version_count(session)
    repeated = sync(full=True)
    assert repeated.counts["unchanged"] == first.counts["new"]
    assert recorded_changes(session, repeated) == {}
    assert version_count(session) == versions

    diff = apply_set(fixtures, DEMO)["tenants"][SLUG]
    changed = sync(full=True)
    assert changed.status == "succeeded"
    assert changed.counts["changed"] == len(ids_of(diff["modified"]))
    assert of_type(recorded_changes(session, changed), "modified") == set(expected(diff).modified)
    assert version_count(session) == versions + changed.counts["new"] + changed.counts["changed"]


def test_a_failed_run_is_recorded_and_leaves_the_previous_data_intact(
    session, source, sync, fixtures
):
    # Arrange
    sync(full=True)
    session.refresh(source)
    watermarks = dict(source.workspace_watermarks)
    versions = version_count(session)
    diff = apply_set(fixtures, DEMO)["tenants"][SLUG]

    def failing_detector(*_):
        raise RuntimeError("change detection broke")

    # Act
    failed = sync(detect_changes=failing_detector)

    # Assert
    assert failed.status == "failed"
    assert failed.error == "RuntimeError: change detection broke"
    assert failed.finished_at is not None
    assert failed.counts["fetched"] > 0
    assert failed.counts["new"] == failed.counts["changed"] == 0
    assert version_count(session) == versions
    session.refresh(source)
    assert source.workspace_watermarks == watermarks
    # The raw payloads stay archived, for reprocessing.
    archived = select(func.count()).where(RawRecordRow.sync_run_id == failed.id)
    assert session.scalar(archived) == failed.counts["fetched"]

    # The watermark did not move, so the next run still finds every change.
    retried = sync()
    assert retried.counts["new"] == len(ids_of(diff["added"]))
    assert retried.counts["changed"] == len(ids_of(diff["modified"]))


def test_a_rejected_api_key_fails_the_run_without_storing_anything(session, sync, mock_app):
    # Arrange
    with mock_client(mock_app, "unknown-tenant") as rejected:
        # Act
        run = sync(full=True, client=rejected)

    # Assert
    assert run.status == "failed"
    assert run.error.startswith("OmerloAuthError")
    assert "401" in run.error
    assert run.counts["fetched"] == 0
    assert version_count(session) == 0


def recorded_changes(session: Session, run: SyncRun) -> dict[tuple[str, str], tuple[str, dict]]:
    """(kind, external id) -> (change type, changed fields) of the run's record_change rows."""
    statement = (
        select(SourceRecord.kind, SourceRecord.external_id, RecordChange)
        .join(RecordChange, RecordChange.source_record_id == SourceRecord.id)
        .where(RecordChange.sync_run_id == run.id)
    )
    return {
        (kind, external_id): (change.change_type, change.changed_fields)
        for kind, external_id, change in session.execute(statement)
    }


def of_type(found, change_type: str) -> set[tuple[str, str]]:
    return {key for key, (found_type, _) in found.items() if found_type == change_type}


@pytest.mark.parametrize("full", [False, True])
def test_a_sync_after_the_demo_mutation_records_the_expected_changes(session, sync, fixtures, full):
    # Arrange
    sync(full=True)
    diff, dropped = apply_demo(fixtures)
    wanted = expected(diff)

    # Act
    run = sync(full=full)

    # Assert
    found = recorded_changes(session, run)
    removed = wanted.removed if full else {("event_session", s) for s in dropped}
    assert of_type(found, "added") == wanted.added
    assert of_type(found, "modified") == set(wanted.modified)
    assert of_type(found, "removed") == removed
    for key, changes in wanted.modified.items():
        assert_fields_correspond(key[0], changes, found[key][1])
    assert run.counts["changes"] == {
        "added": len(wanted.added),
        "modified": len(wanted.modified),
        "removed": len(removed),
    }
    assert run.counts["new"] == len(wanted.added)


def deleted_after_listing(app, *paths: str):
    """The mock server, except that `paths` answer 404 as if deleted after being listed."""
    gone = {"/api" + path for path in paths}

    async def asgi(scope, receive, send):
        if scope["type"] == "http" and scope["path"] in gone:
            response = JSONResponse({"error": "entity not found"}, status_code=404)
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    return asgi


def current_version_id(session: Session, external_id: str):
    return session.scalar(
        select(SourceRecord.id).where(
            SourceRecord.external_id == external_id, SourceRecord.valid_to.is_(None)
        )
    )


def removal_of(session: Session, run: SyncRun, external_id: str) -> RecordChange:
    return session.scalars(
        select(RecordChange)
        .join(SourceRecord, RecordChange.source_record_id == SourceRecord.id)
        .where(RecordChange.sync_run_id == run.id, SourceRecord.external_id == external_id)
    ).one()


@pytest.mark.parametrize("full", [False, True])
def test_a_record_deleted_after_it_was_listed_is_removed_in_any_run(
    session, sync, mock_app, fixtures, full
):
    # Arrange
    sync(full=True)
    diff, dropped = apply_demo(fixtures)
    gone = next(entry["id"] for entry in diff["modified"] if entry["kind"] == "organizations")
    last_version = current_version_id(session, gone)
    detail = COMMUNITY_DETAIL.format(path="organizations", id=gone)

    # Act
    with mock_client(deleted_after_listing(mock_app, detail)) as client:
        run = sync(full=full, client=client)

    # Assert
    assert run.status == "succeeded"
    change = removal_of(session, run, gone)
    assert (change.change_type, change.changed_fields) == ("removed", {})
    assert change.source_record_id == last_version
    assert gone not in current_ids(session)
    removed = of_type(recorded_changes(session, run), "removed")
    seen = expected(diff).removed if full else {("event_session", s) for s in dropped}
    assert removed == seen | {("organization", gone)}
    assert run.counts["removed"] == len(removed)


def test_an_event_deleted_after_it_was_listed_takes_its_sessions(session, sync, mock_app, fixtures):
    # Arrange
    data = tenant_fixtures(fixtures)
    sync(full=True)
    _, [dropped] = apply_demo(fixtures)
    event_id = next(s["event_id"] for s in data.resources["event_sessions"] if s["id"] == dropped)
    sessions = {s["id"] for s in data.resources["event_sessions"] if s["event_id"] == event_id}
    detail = COMMUNITY_DETAIL.format(path="events", id=event_id)

    # Act
    with mock_client(deleted_after_listing(mock_app, detail)) as client:
        run = sync(client=client)

    # Assert
    assert (run.status, run.mode) == ("succeeded", "incremental")
    removed = of_type(recorded_changes(session, run), "removed")
    assert removed == {("event", event_id)} | {("event_session", s) for s in sessions}
    assert not ({event_id} | sessions) & current_ids(session)


def test_a_sync_with_no_source_changes_records_no_changes(session, sync):
    # Arrange
    sync(full=True)

    # Act
    run = sync()

    # Assert
    assert recorded_changes(session, run) == {}
    assert run.counts["changes"] == {"added": 0, "modified": 0, "removed": 0}


def test_sync_command_runs_a_seeded_mock_source(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    with Session(migrated_database) as setup:
        seed(setup)
        setup.commit()
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(
        sync_command, "create_omerlo_client", lambda settings: mock_client(mock_app, SLUG)
    )

    # Act
    sync_command.main(["--tenant", SLUG, "--full"])
    sync_command.main(["--tenant", SLUG])

    # Assert
    lines = capsys.readouterr().out.splitlines()
    full, incremental = [line for line in lines if line.startswith("Sync run")]
    assert "(full) succeeded" in full
    assert "(incremental) succeeded" in incremental
    assert "new 0, changed 0" in incremental


def test_sync_command_exits_with_an_error_when_the_run_fails(
    migrated_database, mock_app, tmp_path, monkeypatch
):
    # Arrange
    with Session(migrated_database) as setup:
        seed(setup)
        setup.commit()
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(
        sync_command, "create_omerlo_client", lambda settings: mock_client(mock_app, "nobody")
    )

    # Act / Assert
    with pytest.raises(SystemExit) as exit_info:
        sync_command.main(["--tenant", SLUG])
    assert exit_info.value.code == 1


def client_for_settings(app):
    """A mock client that sends the key of the source being synced, like the real client."""
    return lambda settings: TestClient(
        app, base_url="http://mock/api", headers={"x-api-key": settings.api_key.get_secret_value()}
    )


def seeded_sources(engine) -> dict[str, uuid.UUID]:
    with Session(engine) as setup:
        seed(setup)
        setup.commit()
        rows = setup.execute(
            select(Tenant.omerlo_media_id, Source.id).join(Source, Source.tenant_id == Tenant.id)
        ).all()
    return dict(rows)


def test_sync_all_runs_every_source_of_every_tenant(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    sources = seeded_sources(migrated_database)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(sync_command, "create_omerlo_client", client_for_settings(mock_app))

    # Act
    sync_command.main(["--all", "--full"])

    # Assert
    lines = [line for line in capsys.readouterr().out.splitlines() if ", source " in line]
    assert len(sources) == 2
    assert len(lines) == len(sources)
    assert all("(full) succeeded" in line for line in lines)
    with Session(migrated_database) as check:
        synced = set(check.scalars(select(SyncRun.source_id)))
    assert synced == set(sources.values())


def test_sync_all_skips_a_source_whose_sync_is_already_running(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    sources = seeded_sources(migrated_database)
    busy = sources[SLUG]
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(sync_command, "create_omerlo_client", client_for_settings(mock_app))

    # Act
    with source_sync_lock(migrated_database, busy):
        sync_command.main(["--all"])

    # Assert
    out = capsys.readouterr().out
    assert f"source {busy}: skipped" in out
    with Session(migrated_database) as check:
        synced = set(check.scalars(select(SyncRun.source_id)))
    assert synced == set(sources.values()) - {busy}


def test_sync_all_keeps_going_after_a_failed_source_and_exits_with_an_error(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    sources = seeded_sources(migrated_database)
    other = next(slug for slug in sources if slug != SLUG)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    working = client_for_settings(mock_app)
    monkeypatch.setattr(
        sync_command,
        "create_omerlo_client",
        lambda settings: (
            mock_client(mock_app, "nobody")
            if settings.api_key.get_secret_value() == api_key_for(SLUG)
            else working(settings)
        ),
    )

    # Act
    with pytest.raises(SystemExit) as exit_info:
        sync_command.main(["--all"])

    # Assert
    assert exit_info.value.code == 1
    with Session(migrated_database) as check:
        statuses = dict(check.execute(select(SyncRun.source_id, SyncRun.status)).all())
    assert statuses == {sources[SLUG]: "failed", sources[other]: "succeeded"}


def test_sync_all_reports_a_source_that_cannot_start_and_syncs_the_rest(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    sources = seeded_sources(migrated_database)
    with Session(migrated_database) as setup:
        setup.execute(
            update(Source).where(Source.id == sources[SLUG]).values(kind="omerlo", config={})
        )
        setup.commit()
    monkeypatch.delenv("OMERLO_API_KEY", raising=False)
    monkeypatch.delenv("OMERLO_BASE_URL", raising=False)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(sync_command, "create_omerlo_client", client_for_settings(mock_app))

    # Act
    with pytest.raises(SystemExit) as exit_info:
        sync_command.main(["--all"])

    # Assert
    out = capsys.readouterr().out
    assert exit_info.value.code == 1
    assert f"source {sources[SLUG]}: could not start" in out
    assert "succeeded" in out


def test_sync_command_rejects_an_unknown_tenant(migrated_database, capsys):
    # Act
    with pytest.raises(SystemExit) as exit_info:
        sync_command.main(["--tenant", "nobody"])

    # Assert
    assert exit_info.value.code == 2
    assert "No tenant 'nobody'" in capsys.readouterr().err


def test_a_sync_classifies_every_current_organization(session, sync):
    # Act
    run = sync(full=True)

    # Assert
    organizations = session.scalars(
        select(SourceRecord.id).where(
            SourceRecord.kind == "organization", SourceRecord.valid_to.is_(None)
        )
    ).all()
    classified = session.scalars(select(IndustryClassification.source_record_id)).all()
    assert run.status == "succeeded"
    assert organizations
    assert set(classified) == set(organizations)


def row_count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def organization_of_each_record(session: Session) -> dict[str, uuid.UUID]:
    """Map each linked current record (by external id) to its organization id."""
    query = (
        select(SourceRecord.external_id, OrgLink.organization_id)
        .join(OrgLink, OrgLink.source_record_id == SourceRecord.id)
        .where(SourceRecord.valid_to.is_(None))
    )
    result = {}
    for external_id, organization_id in session.execute(query):
        result[external_id] = organization_id
    return result


def records_with_a_domain(session: Session) -> set[str]:
    query = select(SourceRecord.external_id).where(
        SourceRecord.valid_to.is_(None), SourceRecord.domain.is_not(None)
    )
    return set(session.scalars(query))


def add_copy_of_organization(fixtures, external_id: str) -> str:
    """Add a copy of an organization with a new id, newer than every other record."""
    path = fixtures / SLUG / "organizations.json"
    records = json.loads(path.read_text(encoding="utf-8"))

    newest = None
    new_record = None
    for record in records:
        updated_at = parse_timestamp(record["updated_at"])
        if newest is None or updated_at > newest:
            newest = updated_at
        if record["id"] == external_id:
            new_record = copy.deepcopy(record)

    new_id = str(uuid.uuid4())
    new_record["id"] = new_id
    new_record["profile"]["id"] = new_id
    new_record["inserted_at"] = iso(newest + timedelta(minutes=1))
    new_record["updated_at"] = new_record["inserted_at"]
    records.append(new_record)
    path.write_text(json.dumps(records), encoding="utf-8")
    return new_id


def test_each_sync_matches_the_records_it_created_or_changed(session, source, sync, fixtures):
    # Arrange
    full = sync(full=True)
    before = organization_of_each_record(session)
    diff = apply_set(fixtures, DEMO)["tenants"][SLUG]
    edited_or_deleted = ids_of(diff["modified"]) | ids_of(diff["removed"])

    # Pick an organization whose records the mutation does not edit or delete.
    edited_organizations = set()
    for external_id in edited_or_deleted:
        if external_id in before:
            edited_organizations.add(before[external_id])
    original = None
    for external_id in sorted(records_with_a_domain(session)):
        if external_id in before and before[external_id] not in edited_organizations:
            original = external_id
            break
    copy_id = add_copy_of_organization(fixtures, original)

    # Act
    incremental = sync()

    # Assert
    # The full sync linked every record it matched.
    assert len(before) > 0
    assert full.counts["matching"]["links_created"] == len(before)
    assert full.counts["matching"]["organizations_created"] == row_count(session, Organization)

    # The incremental sync linked the copy to the same organization as the original.
    after = organization_of_each_record(session)
    assert after[copy_id] == before[original]
    assert after[original] == before[original]
    assert incremental.counts["matching"]["links_created"] >= 1

    # Records the mutation did not touch keep their organization.
    for external_id, organization_id in before.items():
        if external_id not in edited_or_deleted:
            assert after[external_id] == organization_id

    # Matching the whole tenant again finds nothing new.
    assert match_organizations(session, source.tenant_id) == MatchResult()


def test_a_sync_with_no_source_changes_matches_nothing(session, sync):
    # Arrange
    sync(full=True)
    links_before = row_count(session, OrgLink)
    candidates_before = row_count(session, MatchCandidate)

    # Act
    run = sync()

    # Assert
    assert run.counts["matching"] == {
        "organizations_created": 0,
        "organizations_merged": 0,
        "links_created": 0,
        "candidates_created": 0,
    }
    assert row_count(session, OrgLink) == links_before
    assert row_count(session, MatchCandidate) == candidates_before


def test_a_failed_matching_keeps_the_records_and_can_be_run_again(session, sync, monkeypatch):
    # Arrange
    def failing_matcher(*args):
        raise RuntimeError("matcher crashed")

    monkeypatch.setattr(sync_runs, "match_organizations", failing_matcher)
    run = sync(full=True)
    monkeypatch.undo()
    saved_error = run.counts["matching"]
    links_before = row_count(session, OrgLink)

    # Act
    result = match_run(session, run.id)

    # Assert
    assert saved_error == {"error": "RuntimeError: matcher crashed"}
    assert links_before == 0
    assert run.status == "succeeded"
    assert run.counts["new"] == version_count(session)
    assert result.links_created > 0
    assert result.links_created == row_count(session, OrgLink)
    session.refresh(run)
    assert run.counts["matching"]["links_created"] == result.links_created


def test_match_command_reruns_a_sync_run(
    migrated_database, mock_app, tmp_path, monkeypatch, capsys
):
    # Arrange
    with Session(migrated_database) as setup:
        seed(setup)
        setup.commit()
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(
        sync_command, "create_omerlo_client", lambda settings: mock_client(mock_app, SLUG)
    )
    sync_command.main(["--tenant", SLUG, "--full"])
    with Session(migrated_database) as check:
        run_id = check.scalar(select(SyncRun.id))
    capsys.readouterr()

    # Act
    match_command.main(["--sync-run", str(run_id)])

    # Assert
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    by_name = {event["event"]: event for event in events}
    # Orgs then people; the sync already matched everything, so the re-run creates nothing.
    assert by_name["organization_matching_completed"]["links_created"] == 0
    assert by_name["organization_matching_completed"]["organizations_created"] == 0
    assert by_name["people_matching_completed"]["links_created"] == 0
    assert by_name["people_matching_completed"]["people_created"] == 0
    assert events[-1]["event"] == "people_matching_completed"

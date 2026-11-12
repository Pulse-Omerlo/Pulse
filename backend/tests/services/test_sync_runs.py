# AI contribution: 50% or more AI-generated
"""Sync run orchestration with the database mocked; tests/integration runs it for real."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.ingestion.connector import RawRecord
from app.ingestion.files.connector import FileConnector
from app.ingestion.mapped import MappedRecord, MappingResult
from app.models import Source
from app.processing.entity_resolution import MatchResult
from app.services import sync_runs
from app.services.industry import ClassificationReport
from app.services.link_recheck import LINK_RECHECK_KEY, RecheckResult
from app.services.source_records import UpsertResult
from app.services.sync_runs import (
    Fetched,
    FileReader,
    OmerloReader,
    advance_watermarks,
    fetch,
    read_watermarks,
    run_sync,
)

T1 = datetime(2026, 10, 1, tzinfo=UTC)
T2 = T1 + timedelta(days=1)
T3 = T1 + timedelta(days=2)


def raw(kind, external_id, updated_at=None, parent=None):
    return RawRecord(kind, external_id, updated_at, {"id": external_id, "name": "Acme"}, parent)


class FakeConnector:
    """Serves fixed records per (kind, workspace) and remembers each watermark asked for."""

    community_kinds = ("organization", "person")

    def __init__(self, community=None, reference=(), error=None, sessions=True):
        self.community = community or {}
        self.reference = list(reference)
        self.error = error
        self.reads_event_sessions = sessions
        self.calls = []

    def workspaces(self):
        return [{"id": "ws-1"}, {"id": "ws-2"}]

    def fetch_reference(self, workspaces, watermark):
        self.calls.append(("reference", None, watermark))
        return iter(self.reference)

    def fetch_community(self, kind, workspace_id, watermark):
        self.calls.append((kind, workspace_id, watermark))
        if self.error:
            raise self.error
        return iter(self.community.get((kind, workspace_id), []))


def source(**watermarks):
    return Source(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        kind="mock",
        config={},
        watermark_updated_at=watermarks.pop("source", None),
        workspace_watermarks=watermarks.pop("workspaces", {}),
    )


def test_incremental_fetch_reads_each_workspace_and_kind_from_its_own_watermark():
    # Arrange
    stored = {"ws-1": {"organization": T1.isoformat()}, "ws-2": {"person": T2.isoformat()}}
    connector = FakeConnector()

    # Act
    fetch(connector, source(source=T3, workspaces=stored), full=False)

    # Assert
    assert connector.calls == [
        ("reference", None, T3),
        ("organization", "ws-1", T1),
        ("organization", "ws-2", None),
        ("person", "ws-1", None),
        ("person", "ws-2", T2),
    ]


def test_full_fetch_ignores_the_watermarks():
    # Arrange
    stored = {"ws-1": {"organization": T1.isoformat()}}
    connector = FakeConnector()

    # Act
    fetch(connector, source(source=T3, workspaces=stored), full=True)

    # Assert
    assert {watermark for *_, watermark in connector.calls} == {None}


def test_fetch_tracks_the_newest_update_per_workspace_and_kind():
    # Arrange
    connector = FakeConnector(
        community={
            ("organization", "ws-1"): [
                raw("organization", "o2", T2),
                raw("organization_metadata", "o2", parent="o2"),
                raw("organization", "o1", T1),
            ],
            ("person", "ws-2"): [raw("person", "p1", T3)],
        },
        reference=[raw("workspace", "ws-1", T1), raw("category", "c1", T2)],
    )

    # Act
    fetched = fetch(connector, source(), full=True)

    # Assert
    assert len(fetched.records) == 6
    assert fetched.source_newest == T2
    assert fetched.workspace_newest == {"ws-1": {"organization": T2}, "ws-2": {"person": T3}}


def test_fetch_keeps_records_reported_deleted_apart_but_counts_them_for_the_watermark():
    # Arrange
    gone = replace(raw("organization", "o2", T2), deleted=True)
    connector = FakeConnector(
        community={("organization", "ws-1"): [gone, raw("organization", "o1", T1)]}
    )

    # Act
    fetched = fetch(connector, source(), full=False)

    # Assert
    assert [r.external_id for r in fetched.records] == ["o1"]
    assert fetched.deleted == [gone]
    assert fetched.workspace_newest == {"ws-1": {"organization": T2}}


def event_records():
    return [
        raw("event", "e1", T1),
        replace(raw("event_session", "s1", T1, "e1"), payload={"id": "s1", "event_id": "e1"}),
        replace(raw("event", "e2", T1), deleted=True),
    ]


@pytest.mark.parametrize(("sessions", "events"), [(True, {"e1", "e2"}), (False, set())])
def test_fetch_lists_the_events_whose_whole_session_list_was_read(sessions, events):
    # Arrange
    connector = FakeConnector(community={("person", "ws-1"): event_records()}, sessions=sessions)

    # Act
    fetched = fetch(connector, source(), full=False)

    # Assert
    assert fetched.session_events == events


def test_read_watermarks_skips_values_that_are_not_timestamps():
    # Act
    parsed = read_watermarks(
        {"ws-1": {"organization": T1.isoformat(), "person": "soon"}, "ws-2": "broken"}
    )

    # Assert
    assert parsed == {"ws-1": {"organization": T1}}


def test_watermarks_advance_to_the_newest_update_and_never_go_back():
    # Arrange
    target = source(source=T2, workspaces={"ws-1": {"organization": T2.isoformat()}})
    fetched = Fetched(
        source_newest=T1,
        workspace_newest={"ws-1": {"organization": T1, "person": T3}, "ws-2": {"event": T1}},
    )

    # Act
    advance_watermarks(target, fetched)

    # Assert
    assert target.watermark_updated_at == T2
    assert target.workspace_watermarks == {
        "ws-1": {"organization": T2.isoformat(), "person": T3.isoformat()},
        "ws-2": {"event": T1.isoformat()},
    }


@pytest.fixture
def session(mocker):
    # A real info dict, where the session keeps its tenant scope.
    return mocker.MagicMock(info={})


@pytest.fixture(autouse=True)
def classified(mocker):
    """The industry classification run after a successful sync, mocked."""
    return mocker.patch.object(
        sync_runs, "classify_organizations", return_value=ClassificationReport()
    )


@pytest.fixture(autouse=True)
def matched(mocker):
    """The Tier 1 organization matching run after a successful sync, mocked."""
    return mocker.patch.object(sync_runs, "match_organizations", return_value=MatchResult())


@pytest.fixture(autouse=True)
def measured(mocker):
    """The sector mix computed after a successful sync, mocked."""
    return mocker.patch.object(sync_runs, "measure_sector_mix")


@pytest.fixture(autouse=True)
def matched_people(mocker):
    """The Tier 1 people matching run after a successful sync, mocked."""
    from app.processing.person_resolution import PersonMatchResult

    return mocker.patch.object(sync_runs, "match_people", return_value=PersonMatchResult())


@pytest.fixture(autouse=True)
def rechecked(mocker):
    """Automatic link recheck inside store, mocked."""
    return mocker.patch.object(sync_runs, "recheck_links", return_value=RecheckResult())


@pytest.fixture
def stored(mocker):
    """The archive and the versioned upsert, mocked."""
    archive = mocker.patch.object(sync_runs, "archive_records")
    upsert = mocker.patch.object(
        sync_runs, "upsert_records", return_value=UpsertResult(["o1"], ["o2"], ["o3", "o4"])
    )
    close = mocker.patch.object(sync_runs, "close_unseen", return_value=["o5"])
    return archive, upsert, close


def clock():
    moments = iter([T1, T2, T3])
    return lambda: next(moments)


def org_reader():
    records = [raw("organization", "o1", T1)]
    return OmerloReader(FakeConnector(community={("organization", "ws-1"): records}))


def test_a_successful_run_records_its_counts_and_advances_the_watermarks(session, stored):
    # Arrange
    target = source()
    detected = []

    # Act
    run = run_sync(
        session,
        target,
        org_reader(),
        archive=None,
        full=True,
        now=clock(),
        detect_changes=lambda *args: detected.append(args),
    )

    # Assert
    assert (run.status, run.mode, run.started_at, run.finished_at) == ("succeeded", "full", T1, T2)
    assert run.counts == {
        "fetched": 1,
        "cleaning_flags": {},
        LINK_RECHECK_KEY: {
            "checked": 0,
            "kept": 0,
            "dropped": 0,
            "unverifiable": 0,
        },
        "new": 1,
        "changed": 1,
        "unchanged": 2,
        "removed": 1,
        "failed": 0,
        "matching": {
            "organizations_created": 0,
            "organizations_merged": 0,
            "links_created": 0,
            "candidates_created": 0,
        },
        "matching_people": {
            "people_created": 0,
            "people_merged": 0,
            "links_created": 0,
            "candidates_created": 0,
        },
    }
    assert target.workspace_watermarks == {"ws-1": {"organization": T1.isoformat()}}
    assert detected == [(session, run)]
    # Start, archive, store, org and people matching, classification, then the sector mix.
    assert session.commit.call_count == 7


def test_an_incremental_run_never_marks_records_removed(session, stored):
    # Arrange
    _, _, close = stored

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert (run.mode, run.counts["removed"]) == ("incremental", 0)
    close.assert_not_called()


@pytest.mark.parametrize("full", [False, True])
def test_records_reported_deleted_are_removed_in_any_run(session, stored, mocker, full):
    # Arrange
    archive, upsert, _ = stored
    close_deleted = mocker.patch.object(sync_runs, "close_deleted", return_value=["o9"])
    gone = replace(raw("organization", "o9", T1), deleted=True)
    connector = FakeConnector(
        community={("organization", "ws-1"): [raw("organization", "o1", T1), gone]}
    )

    # Act
    run = run_sync(session, source(), OmerloReader(connector), archive=None, full=full, now=clock())

    # Assert
    assert run.status == "succeeded"
    close_deleted.assert_called_once_with(session, run, "organization", {"o9"})
    # The mocked close_unseen removes o5 in a full run.
    assert run.counts["removed"] == (2 if full else 1)
    assert run.counts["fetched"] == 1
    assert [r.external_id for r in archive.call_args.args[3]] == ["o1"]
    assert [r.external_id for r in upsert.call_args.args[2]] == ["o1"]


def test_sessions_missing_from_a_read_event_are_removed(session, stored, mocker):
    # Arrange
    dropped = mocker.patch.object(sync_runs, "close_dropped_children", return_value=["s2"])
    mocker.patch.object(sync_runs, "close_deleted", return_value=[])
    connector = FakeConnector(community={("person", "ws-1"): event_records()})

    # Act
    run = run_sync(session, source(), OmerloReader(connector), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    dropped.assert_called_once_with(
        session, run, "event_session", "event_id", {"e1", "e2"}, keep=set()
    )
    assert run.counts["removed"] == 1


def test_a_fetch_error_fails_the_run_and_stores_nothing(session, stored):
    # Arrange
    archive, upsert, _ = stored
    target = source()
    connector = FakeConnector(error=httpx.ConnectError("connection refused"))

    # Act
    run = run_sync(session, target, OmerloReader(connector), archive=None, now=clock())

    # Assert
    assert run.status == "failed"
    assert run.error == "ConnectError: connection refused"
    assert run.counts == dict.fromkeys(sync_runs.COUNT_KEYS, 0)
    assert run.finished_at == T2
    archive.assert_not_called()
    upsert.assert_not_called()
    session.rollback.assert_called_once()
    assert target.workspace_watermarks == {}


def test_a_storage_error_keeps_only_the_fetched_count(session, stored):
    # Arrange
    _, upsert, _ = stored
    upsert.side_effect = RuntimeError("x" * 5000)

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "failed"
    assert run.counts == {**dict.fromkeys(sync_runs.COUNT_KEYS, 0), "fetched": 1}
    assert len(run.error) == sync_runs.MAX_ERROR_LENGTH


def test_cleaning_counts_are_persisted_and_only_unique_records_reach_storage(session, stored):
    _, upsert, _ = stored
    record = raw("organization", "o1", T1)
    connector = FakeConnector(community={("organization", "ws-1"): [record, record]})

    run = run_sync(session, source(), OmerloReader(connector), archive=None, now=clock())

    assert run.status == "succeeded"
    assert run.counts["cleaning_flags"] == {"duplicate in current batch": 1}
    assert len(upsert.call_args.args[2]) == 1


class FakeReader:
    """A source that is not Omerlo: fixed records, mapped by a fixed result."""

    def __init__(self, records, mapped, kinds=None):
        self.records = records
        self.mapped = mapped
        self.kinds = kinds

    def fetch(self, source, *, full):
        return Fetched(records=list(self.records), kinds=self.kinds)

    def map(self, records):
        return self.mapped


def test_any_reader_can_feed_a_sync_run(session, stored):
    # Arrange
    _, upsert, _ = stored
    mapped = MappedRecord(
        kind="organization", external_id="f1", workspace_id=None, name_normalized="acme"
    )
    reader = FakeReader([raw("organization", "f1")], MappingResult((mapped,), ()))

    # Act
    run = run_sync(session, source(), reader, archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    assert upsert.call_args.args[2] == (mapped,)
    assert run.counts["fetched"] == 1


def test_a_full_run_only_removes_records_of_the_kinds_it_read(session, stored):
    # Arrange
    _, _, close = stored
    reader = FakeReader([], MappingResult((), ()), kinds=("person",))

    # Act
    run_sync(session, source(), reader, archive=None, full=True, now=clock())

    # Assert
    assert close.call_args.kwargs["kinds"] == ("person",)


def test_a_file_import_counts_rejected_rows_and_never_removes_them(session, stored):
    # Arrange
    _, upsert, close = stored
    data = b"id,name,email\n1,Acme,info@acme.ca\n2,Beta,not-an-email\n"
    reader = FileReader(FileConnector(data, "organizations.csv", "organization"))

    # Act
    run = run_sync(session, source(), reader, archive=None, full=True, now=clock())

    # Assert
    assert run.status == "succeeded"
    assert run.counts["fetched"] == 1
    assert run.counts["failed"] == 1
    assert [r.external_id for r in upsert.call_args.args[2]] == ["organization:1"]
    assert close.call_args.kwargs == {"keep": {"organization:2"}, "kinds": ("organization",)}
    assert run.counts["failures"] == [
        {"id": "organization:2", "reason": "row 3: email is not an email address"}
    ]


def test_only_the_first_failures_are_kept_on_the_run(session, stored):
    # Arrange
    rows = "".join(f"{n},Org {n},bad\n" for n in range(sync_runs.MAX_REPORTED_FAILURES + 5))
    data = ("id,name,email\n" + rows).encode()
    reader = FileReader(FileConnector(data, "organizations.csv", "organization"))

    # Act
    run = run_sync(session, source(), reader, archive=None, now=clock())

    # Assert
    assert run.counts["failed"] == sync_runs.MAX_REPORTED_FAILURES + 5
    assert len(run.counts["failures"]) == sync_runs.MAX_REPORTED_FAILURES


def test_a_file_import_archives_the_file_without_counting_or_mapping_it(session, stored):
    # Arrange
    archive, upsert, _ = stored
    data = b"id,name\n1,Acme\n"
    reader = FileReader(FileConnector(data, "organizations.csv", "organization"))

    # Act
    run = run_sync(session, source(), reader, archive=None, now=clock())

    # Assert
    archived = archive.call_args.args[3]
    assert [r.entity_kind for r in archived] == ["organization", "import_file"]
    assert run.counts["fetched"] == 1
    assert len(upsert.call_args.args[2]) == 1


def test_required_cleaning_failure_does_not_abort_sync_or_remove_stored_record(session, stored):
    # Arrange
    _, upsert, close = stored
    connector = FakeConnector(
        community={
            ("organization", "ws-1"): [
                raw("organization", "NA", T1),
                raw("organization", "valid-org", T1),
            ],
        }
    )

    # Act
    run = run_sync(session, source(), OmerloReader(connector), archive=None, full=True, now=clock())

    # Assert
    assert run.status == "succeeded"
    assert run.counts["failed"] == 1
    assert run.counts["cleaning_flags"] == {"required field is missing": 1}
    assert [r.external_id for r in upsert.call_args.args[2]] == ["valid-org"]
    assert close.call_args.kwargs["keep"] == {"NA"}
    session.rollback.assert_not_called()


def test_a_successful_run_classifies_the_tenant_organizations(session, stored, classified):
    # Arrange
    target = source()

    # Act
    run = run_sync(session, target, org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    classified.assert_called_once_with(session, target.tenant_id)


def test_a_failed_run_classifies_nothing(session, stored, classified):
    # Arrange
    _, upsert, _ = stored
    upsert.side_effect = RuntimeError("database went away")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "failed"
    classified.assert_not_called()


def test_a_failed_classification_leaves_the_run_succeeded(session, stored, classified):
    # Arrange
    classified.side_effect = ValueError("invalid sectors for category 'abc'")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    session.rollback.assert_called_once()


def test_a_successful_run_matches_the_versions_it_opened(
    mocker, session, stored, matched, matched_people
):
    # Arrange
    from app.processing.person_resolution import PersonMatchResult

    target = source()
    changed_ids = [uuid.uuid4(), uuid.uuid4()]
    person_ids = [uuid.uuid4()]
    found_ids = mocker.patch.object(sync_runs, "changed_organization_ids", return_value=changed_ids)
    found_people = mocker.patch.object(sync_runs, "changed_person_ids", return_value=person_ids)
    matched.return_value = MatchResult(
        organizations_created=1, links_created=3, candidates_created=2, organizations_merged=2
    )
    matched_people.return_value = PersonMatchResult(
        people_created=1, links_created=2, candidates_created=0, people_merged=1
    )

    # Act
    run = run_sync(session, target, org_reader(), archive=None, now=clock())

    # Assert
    found_ids.assert_called_once_with(session, run)
    found_people.assert_called_once_with(session, run)
    matched.assert_called_once_with(session, target.tenant_id, changed_ids)
    matched_people.assert_called_once_with(session, target.tenant_id, person_ids)
    assert run.counts["matching"] == {
        "organizations_created": 1,
        "organizations_merged": 2,
        "links_created": 3,
        "candidates_created": 2,
    }
    assert run.counts["matching_people"] == {
        "people_created": 1,
        "people_merged": 1,
        "links_created": 2,
        "candidates_created": 0,
    }


def test_a_failed_run_matches_nothing(session, stored, matched, matched_people):
    # Arrange
    _, upsert, _ = stored
    upsert.side_effect = RuntimeError("database went away")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "failed"
    assert "matching" not in run.counts
    assert "matching_people" not in run.counts
    matched.assert_not_called()
    matched_people.assert_not_called()


def test_a_failed_matching_is_recorded_and_keeps_the_run_and_classification(
    session, stored, matched, matched_people, classified
):
    # Arrange
    matched.side_effect = ValueError("generic_domains.toml needs a non-empty domains list")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    assert run.counts["new"] == 1
    assert run.counts["matching"] == {
        "error": "ValueError: generic_domains.toml needs a non-empty domains list"
    }
    assert run.counts["matching_people"] == {
        "people_created": 0,
        "people_merged": 0,
        "links_created": 0,
        "candidates_created": 0,
    }
    session.rollback.assert_called_once()
    classified.assert_called_once()
    matched_people.assert_called_once()


def test_a_failed_people_match_keeps_org_matching_counts(
    session, stored, matched, matched_people, classified
):
    # Arrange
    matched.return_value = MatchResult(organizations_created=1, links_created=1)
    matched_people.side_effect = ValueError("people matcher blew up")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    assert run.counts["matching"] == {
        "organizations_created": 1,
        "organizations_merged": 0,
        "links_created": 1,
        "candidates_created": 0,
    }
    assert run.counts["matching_people"] == {"error": "ValueError: people matcher blew up"}
    classified.assert_called_once()


def test_a_successful_run_measures_the_sector_mix_after_classifying(
    session, stored, classified, measured, mocker
):
    # Arrange
    steps = mocker.Mock()
    steps.attach_mock(classified, "classify")
    steps.attach_mock(measured, "measure")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert [call[0] for call in steps.mock_calls] == ["classify", "measure"]
    measured.assert_called_once_with(session, run)


def test_a_failed_run_measures_nothing(session, stored, measured):
    # Arrange
    _, upsert, _ = stored
    upsert.side_effect = RuntimeError("database went away")

    # Act
    run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    measured.assert_not_called()


def test_the_sector_mix_is_measured_even_when_classification_fails(
    session, stored, classified, measured
):
    # Arrange
    classified.side_effect = ValueError("invalid sectors for category 'abc'")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    measured.assert_called_once()


def test_a_failed_sector_mix_leaves_the_run_succeeded(session, stored, measured):
    # Arrange
    measured.side_effect = ValueError("tenant.toml: unknown sector '99'")

    # Act
    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    # Assert
    assert run.status == "succeeded"
    session.rollback.assert_called_once()


def test_recheck_runs_after_close_and_before_detect(session, stored, rechecked):
    order: list[str] = []
    _, _, close = stored
    close.side_effect = lambda *args, **kwargs: order.append("close") or ["o5"]
    rechecked.side_effect = lambda *args, **kwargs: (
        order.append("recheck") or RecheckResult(checked=1, kept=0, dropped=1, unverifiable=0)
    )

    run = run_sync(
        session,
        source(),
        org_reader(),
        archive=None,
        full=True,
        now=clock(),
        detect_changes=lambda *args: order.append("detect") or {},
    )

    assert order == ["close", "recheck", "detect"]
    assert run.counts[LINK_RECHECK_KEY] == {
        "checked": 1,
        "kept": 0,
        "dropped": 1,
        "unverifiable": 0,
    }
    rechecked.assert_called_once()
    assert rechecked.call_args.args[2] == []
    assert session.commit.call_count == 7


def test_recheck_receives_carried_pairs(session, stored, rechecked):
    _, upsert, _ = stored
    carried = [(uuid.uuid4(), uuid.uuid4())]
    upsert.return_value = UpsertResult(["o1"], carried=carried)

    run_sync(session, source(), org_reader(), archive=None, now=clock())

    assert rechecked.call_args.args[2] == carried


def test_a_fetch_error_does_not_recheck(session, stored, rechecked):
    archive, upsert, _ = stored
    connector = FakeConnector(error=httpx.ConnectError("connection refused"))

    run_sync(session, source(), OmerloReader(connector), archive=None, now=clock())

    rechecked.assert_not_called()
    archive.assert_not_called()
    upsert.assert_not_called()


def test_a_recheck_error_fails_the_run_and_rolls_store_back(session, stored, rechecked):
    rechecked.side_effect = RuntimeError("evidence check failed")

    run = run_sync(session, source(), org_reader(), archive=None, now=clock())

    assert run.status == "failed"
    assert run.counts == {**dict.fromkeys(sync_runs.COUNT_KEYS, 0), "fetched": 1}
    assert "link_recheck" not in run.counts
    session.rollback.assert_called_once()

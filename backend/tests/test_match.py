# AI contribution: 50% or more AI-generated
"""The match command's checks, with the database mocked."""

import uuid

import pytest

from app import match
from app.models import SyncRun
from app.sync import SyncCommandError


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(info={})


def test_rerunning_an_unknown_sync_run_is_refused(session):
    # Arrange
    session.get.return_value = None

    # Act / Assert
    with pytest.raises(SyncCommandError, match="No sync run"):
        match.match_run(session, uuid.uuid4())


def test_rerunning_a_failed_sync_run_is_refused(session):
    # Arrange
    session.get.return_value = SyncRun(tenant_id=uuid.uuid4(), status="failed")

    # Act / Assert
    with pytest.raises(SyncCommandError, match="did not succeed"):
        match.match_run(session, uuid.uuid4())


def test_a_failed_rerun_reports_the_matching_error(mocker, session):
    # Arrange
    run = SyncRun(
        tenant_id=uuid.uuid4(), status="succeeded", counts={"matching": {"error": "boom"}}
    )
    session.get.return_value = run
    mocker.patch.object(match, "match_after_sync", return_value=None)

    # Act / Assert
    with pytest.raises(SyncCommandError, match="Matching failed: boom"):
        match.match_run(session, uuid.uuid4())


def test_a_failed_people_rerun_reports_the_people_error(mocker, session):
    # Arrange
    from app.processing.entity_resolution import MatchResult

    run = SyncRun(
        tenant_id=uuid.uuid4(),
        status="succeeded",
        counts={"matching": {}, "matching_people": {"error": "people boom"}},
    )
    session.get.return_value = run
    mocker.patch.object(match, "match_after_sync", return_value=MatchResult())
    mocker.patch.object(match, "log_org_summary")

    # Act / Assert
    with pytest.raises(SyncCommandError, match="People matching failed: people boom"):
        match.match_run(session, uuid.uuid4())


def test_the_command_needs_a_tenant_or_a_sync_run():
    # Act / Assert
    with pytest.raises(SystemExit) as exit_info:
        match.main([])
    assert exit_info.value.code == 2

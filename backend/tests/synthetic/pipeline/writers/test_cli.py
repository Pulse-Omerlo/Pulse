# AI contribution: 50% or more AI-generated
import json
import subprocess

import pytest

from app.ingestion.omerlo.client import OmerloAuthError
from synthetic.pipeline.writers.base import WriteRejected
from synthetic.pipeline.writers.cli import CliWriter


class Runner:
    """Stands in for subprocess.run: records each call and answers with fixed output."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, json.loads(kwargs["input"])))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def test_each_call_runs_the_command_with_the_operation_and_json_arguments():
    # Arrange
    runner = Runner(stdout=json.dumps({"result": "org-9"}))
    writer = CliWriter(["omerlo-cli", "--profile", "staging"], run=runner)

    # Act
    record_id = writer.create("organizations", "ws-1", {"name": "Bistro Roy"})

    # Assert
    assert record_id == "org-9"
    args, arguments = runner.calls[0]
    assert args == ["omerlo-cli", "--profile", "staging", "create"]
    assert arguments == {
        "kind": "organizations",
        "workspace_id": "ws-1",
        "payload": {"name": "Bistro Roy"},
    }


@pytest.mark.parametrize(
    "call",
    [
        lambda w: w.list_workspaces(),
        lambda w: w.list_records("people", "ws"),
        lambda w: w.get_metadata("people", "p"),
        lambda w: w.set_metadata("people", "p", []),
        lambda w: w.get_employer("p", "b"),
        lambda w: w.set_employer("p", "o", "b"),
    ],
)
def test_every_operation_goes_through_the_command(call):
    # Arrange
    runner = Runner(stdout=json.dumps({"result": None}))

    # Act
    call(CliWriter(["cli"], run=runner))

    # Assert
    assert len(runner.calls) == 1


def test_an_error_answer_is_a_rejection_with_its_status():
    # Arrange
    writer = CliWriter(["cli"], run=Runner(stdout=json.dumps({"error": "bad", "status": 422})))

    # Act / Assert
    with pytest.raises(WriteRejected, match="422 bad"):
        writer.set_metadata("people", "p", [])


def test_an_auth_error_answer_stops_the_run():
    # Arrange
    writer = CliWriter(["cli"], run=Runner(stdout=json.dumps({"error": "no", "status": 403})))

    # Act / Assert
    with pytest.raises(OmerloAuthError):
        writer.list_workspaces()


def test_output_that_is_not_json_is_a_rejection_naming_stderr():
    # Arrange
    writer = CliWriter(["cli"], run=Runner(stdout="oops", returncode=1, stderr="not logged in"))

    # Act / Assert
    with pytest.raises(WriteRejected, match="no JSON answer \\(not logged in\\)"):
        writer.list_workspaces()


def test_a_command_that_hangs_is_a_rejection():
    # Arrange
    runner = Runner(raises=subprocess.TimeoutExpired(["cli"], 5))
    writer = CliWriter(["cli"], run=runner, timeout=5)

    # Act / Assert
    with pytest.raises(WriteRejected, match="timed out after 5s"):
        writer.get_employer("p", "b")


def test_a_cli_writer_needs_a_command():
    # Act / Assert
    with pytest.raises(ValueError, match="needs a command"):
        CliWriter([])

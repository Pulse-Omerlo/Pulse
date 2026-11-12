# AI contribution: 50% or more AI-generated
from pathlib import Path

from synthetic.pipeline.__main__ import (
    EXIT_AUTH,
    EXIT_REFUSED,
    EXIT_REJECTIONS,
    EXIT_STOPPED,
    main,
    parse_args,
)
from synthetic.pipeline.mixer import load_mix
from synthetic.pipeline.plan import build_plan
from synthetic.pipeline.target import load_target
from synthetic.pipeline.writers.base import WriteRejected
from synthetic.pipeline.writers.fake import FakeWriter

MIX = """
name = "cli-test"
seed = 3
organizations = 6
people = 8
locales = ["fr"]

[sectors]
"72" = 1.0

[duplicates]
organizations = 0
people = 0
"""


def mix_file(tmp_path, text=MIX):
    path = tmp_path / "mix.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def staging_writer():
    return FakeWriter((load_target().workspace_id,))


def entry(log_output, event):
    return next(e for e in log_output() if e["event"] == event)


def test_the_files_sink_writes_fixtures_for_the_mix(tmp_path, log_output):
    # Act
    code = main(["--mix", mix_file(tmp_path), "--out", str(tmp_path / "out")])

    # Assert
    assert code == 0
    assert (tmp_path / "out" / "mix-cli-test" / "organizations.json").exists()
    logged = entry(log_output, "fixtures_written")
    assert (logged["mix"], logged["seed"], logged["organizations"]) == ("cli-test", 3, 6)


def test_the_omerlo_sink_logs_the_report(tmp_path, log_output):
    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=staging_writer())

    # Assert
    assert code == 0
    logged = entry(log_output, "omerlo_run_completed")
    assert (logged["level"], logged["organizations_created"]) == ("info", 6)


def test_a_broken_mix_file_is_refused_with_its_reason(tmp_path, log_output):
    # Arrange
    broken = MIX.replace('"72" = 1.0', '"72" = 0.5')

    # Act
    code = main(["--mix", mix_file(tmp_path, broken)])

    # Assert
    assert code == EXIT_REFUSED
    assert "sector shares sum to 0.5" in entry(log_output, "mix_refused")["reason"]


def test_a_guard_refusal_writes_nothing(tmp_path, log_output):
    # Arrange
    writer = FakeWriter(("some-other-workspace",))

    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_REFUSED
    assert entry(log_output, "omerlo_run_refused")["level"] == "error"


def test_an_auth_error_logs_what_was_done_and_stops(tmp_path, log_output):
    # Arrange
    writer = staging_writer()
    writer.auth_error = True

    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_AUTH
    logged = entry(log_output, "omerlo_run_stopped")
    assert (logged["level"], logged["organizations_created"]) == ("error", 0)


def test_rejections_make_the_run_exit_with_one(tmp_path, log_output):
    # Arrange
    path = mix_file(tmp_path)
    first_org = build_plan(load_mix(Path(path))).organizations[0].record["name"]
    writer = staging_writer()
    writer.refuse(first_org, times=2)

    # Act
    code = main(["--mix", path, "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_REJECTIONS
    rejected = entry(log_output, "record_rejected")
    assert (rejected["level"], rejected["kind"]) == ("warning", "organizations")


def test_the_default_output_folder_leaves_the_demo_fixtures_alone():
    # Act
    args = parse_args(["--mix", "mix.toml"])

    # Assert
    assert args.out.name == "mixes"
    assert args.out.parent.name == "out"


def test_a_stopped_run_logs_what_was_done_and_exits_with_four(tmp_path, log_output):
    # Arrange
    writer = staging_writer()

    def refuse(kind, record_id, entries):
        raise WriteRejected("metadata.key is invalid", 400)

    writer.set_metadata = refuse

    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_STOPPED
    logged = entry(log_output, "omerlo_run_stopped")
    assert logged["organizations_rejected"] == 1
    assert "tag or delete it before running again" in logged["reason"]

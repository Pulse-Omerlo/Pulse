# AI contribution: 50% or more AI-generated
from pathlib import Path

from synthetic.pipeline.__main__ import EXIT_AUTH, EXIT_REFUSED, EXIT_REJECTIONS, main
from synthetic.pipeline.mixer import load_mix
from synthetic.pipeline.plan import build_plan
from synthetic.pipeline.target import load_target
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


def test_the_files_sink_writes_fixtures_for_the_mix(tmp_path, capsys):
    # Act
    code = main(["--mix", mix_file(tmp_path), "--out", str(tmp_path / "out")])

    # Assert
    assert code == 0
    assert (tmp_path / "out" / "mix-cli-test" / "organizations.json").exists()
    assert "mix cli-test, seed 3" in capsys.readouterr().out


def test_the_omerlo_sink_prints_the_report(tmp_path, capsys):
    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=staging_writer())

    # Assert
    assert code == 0
    assert "organizations  created    6" in capsys.readouterr().out


def test_a_broken_mix_file_is_refused_with_its_reason(tmp_path, capsys):
    # Arrange
    broken = MIX.replace('"72" = 1.0', '"72" = 0.5')

    # Act
    code = main(["--mix", mix_file(tmp_path, broken)])

    # Assert
    assert code == EXIT_REFUSED
    assert "sector shares sum to 0.5" in capsys.readouterr().err


def test_a_guard_refusal_writes_nothing(tmp_path, capsys):
    # Arrange
    writer = FakeWriter(("some-other-workspace",))

    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_REFUSED
    assert "Refused, nothing was written" in capsys.readouterr().err


def test_an_auth_error_prints_what_was_done_and_stops(tmp_path, capsys):
    # Arrange
    writer = staging_writer()
    writer.auth_error = True

    # Act
    code = main(["--mix", mix_file(tmp_path), "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_AUTH
    captured = capsys.readouterr()
    assert "organizations  created    0" in captured.out
    assert "Stopped:" in captured.err


def test_rejections_make_the_run_exit_with_one(tmp_path):
    # Arrange
    path = mix_file(tmp_path)
    first_org = build_plan(load_mix(Path(path))).organizations[0].record["name"]
    writer = staging_writer()
    writer.refuse(first_org, times=2)

    # Act
    code = main(["--mix", path, "--sink", "omerlo"], writer=writer)

    # Assert
    assert code == EXIT_REJECTIONS

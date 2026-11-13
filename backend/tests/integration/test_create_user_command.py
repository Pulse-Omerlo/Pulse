# AI contribution: 50% or more AI-generated
import pytest

from app import create_user as command
from app import seed as seed_module

pytestmark = pytest.mark.integration


def test_the_command_creates_a_user_in_the_tenant(migrated_database, monkeypatch, capsys):
    # Arrange
    seed_module.main()
    monkeypatch.setenv("PULSE_USER_PASSWORD", "a long enough password")

    # Act
    command.main(["--tenant", "demo-capitale", "--email", "Ana@Example.CA"])

    # Assert
    assert capsys.readouterr().out.strip().endswith("Created ana@example.ca in Média démo Capitale")


def test_the_command_refuses_a_short_password(migrated_database, monkeypatch, capsys):
    # Arrange
    seed_module.main()
    monkeypatch.setenv("PULSE_USER_PASSWORD", "short")

    # Act
    with pytest.raises(SystemExit) as exit_info:
        command.main(["--tenant", "demo-capitale", "--email", "bob@example.ca"])

    # Assert
    assert exit_info.value.code == 2
    assert "at least 12 characters" in capsys.readouterr().err

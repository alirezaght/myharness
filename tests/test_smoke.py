import pytest

import harness
from harness.cli import main


def test_package_imports_with_version():
    assert harness.__version__ == "0.1.0"


def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_cli_resume_without_state_lists_nothing(isolated_home, capsys):
    assert main(["resume"]) == 0
    assert capsys.readouterr().out == "no tasks to resume\n"
    assert not isolated_home.exists()


def test_cli_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert capsys.readouterr().err != ""


def test_cli_rejects_unknown_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 2

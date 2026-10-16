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


@pytest.mark.parametrize("argv", [["resume"]])
def test_cli_stub_commands_exit_2(argv, capsys):
    assert main(argv) == 2
    assert "not implemented" in capsys.readouterr().err


def test_cli_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert capsys.readouterr().err != ""


def test_cli_rejects_unknown_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 2

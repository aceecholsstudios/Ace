from __future__ import annotations

from pathlib import Path

import pytest

from ace import __version__
from ace.app import EXIT_NOT_IMPLEMENTED, main
from tests.conftest import CONFIG_DIR


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_doctor_runs_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "doctor",
            "--offline",
            "--config",
            str(CONFIG_DIR / "ace.yaml"),
            "--fees",
            str(CONFIG_DIR / "fees.yaml"),
            "--env-file",
            f"{tmp_path}/absent.env",
            "--data-dir",
            f"{tmp_path}/data",
        ]
    )
    out = capsys.readouterr().out
    assert "[OK  ] config" in out
    assert "[FAIL] credentials" in out
    assert code == 1


@pytest.mark.parametrize("command", ["run", "report"])
def test_unimplemented_commands(command: str) -> None:
    assert main([command]) == EXIT_NOT_IMPLEMENTED

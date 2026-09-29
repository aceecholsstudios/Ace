from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_secrets import find_problems, main
from tests.conftest import REPO_ROOT


def test_env_example_passes() -> None:
    path = REPO_ROOT / ".env.example"
    assert find_problems(path, path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", [".env", ".env.local", "sub/.env"])
def test_env_files_are_blocked(name: str) -> None:
    assert find_problems(Path(name), "")


@pytest.mark.parametrize(
    "text",
    [
        "TRADOVATE_PASSWORD=hunter2\n",
        "export DATABENTO_API_KEY=db-abc\n",
        "  TRADOVATE_SECRET: xyz\n",
    ],
)
def test_assignments_with_values_are_blocked(text: str) -> None:
    [problem] = find_problems(Path("notes.md"), text)
    assert "has a value" in problem
    assert "hunter2" not in problem


@pytest.mark.parametrize(
    "text", ["TRADOVATE_PASSWORD=\n", "TRADOVATE_PASSWORD=...\n", "mentions TRADOVATE_CID only\n"]
)
def test_placeholders_pass(text: str) -> None:
    assert find_problems(Path("docs.md"), text) == []


def test_main_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.txt"
    good.write_text("nothing here\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("TRADOVATE_CID=42\n", encoding="utf-8")
    assert main([str(good)]) == 0
    assert main([str(good), str(bad)]) == 1
    assert "bad.txt:1: TRADOVATE_CID has a value" in capsys.readouterr().err

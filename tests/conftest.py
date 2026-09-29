from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture
def ace_yaml_text() -> str:
    return (CONFIG_DIR / "ace.yaml").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_credentials_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real credentials out of every test."""
    for key in (
        "TRADOVATE_USERNAME",
        "TRADOVATE_PASSWORD",
        "TRADOVATE_CID",
        "TRADOVATE_SECRET",
        "TRADOVATE_ENV",
        "DATABENTO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

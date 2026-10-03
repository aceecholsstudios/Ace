"""Dashboard smoke test: play a synthetic session through the real window (offscreen) and grab it."""

import os
from datetime import date

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("qasync")


def test_dashboard_renders_demo_session(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from wall2.config import Wall2Config
    from wall2.ui.run import run_demo_ui

    snap = tmp_path / "ui.png"
    rc = run_demo_ui(Wall2Config(), date(2026, 10, 2), tmp_path / "out", 0.02, 0.0, snap, 120)
    assert rc == 0 and snap.is_file() and snap.stat().st_size > 10_000

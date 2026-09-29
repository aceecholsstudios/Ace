from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from ace.ops import windows
from ace.ops.clock import DriftSample, NtpError
from ace.ops.doctor import Check, DoctorOptions, Status, exit_code, format_report, run_checks
from ace.ops.windows import CommandResult
from tests.conftest import CONFIG_DIR
from tests.unit.test_secrets import VALUES
from tests.unit.test_windows_ops import POWERCFG, REG, W32TM


def _drift(offset: float) -> DriftSample:
    return DriftSample(server="ntp.test", offset_sec=offset, round_trip_sec=0.01)


def _options(tmp_path: Path, **overrides: object) -> DoctorOptions:
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in VALUES.items()), encoding="utf-8")
    base: dict[str, object] = {
        "config_path": CONFIG_DIR / "ace.yaml",
        "fees_path": CONFIG_DIR / "fees.yaml",
        "env_file": env,
        "data_dir": tmp_path / "data",
        "platform": "linux",
        "drift": lambda server: _drift(0.012),
    }
    base.update(overrides)
    return DoctorOptions(**base)  # type: ignore[arg-type]


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_healthy_setup_passes(tmp_path: Path) -> None:
    checks = run_checks(_options(tmp_path))
    by_name = _by_name(checks)
    assert by_name["config"].status is Status.OK
    assert by_name["credentials"].status is Status.OK
    assert by_name["data dir"].status is Status.OK
    assert by_name["clock drift"].status is Status.OK
    assert by_name["fees"].status is Status.WARN  # shipped fees are unverified
    assert by_name["sleep on AC"].status is Status.SKIP
    assert exit_code(checks) == 0
    assert (tmp_path / "data").is_dir()


def test_missing_env_file_fails_with_hint(tmp_path: Path) -> None:
    checks = run_checks(_options(tmp_path, env_file=tmp_path / "missing.env"))
    creds = _by_name(checks)["credentials"]
    assert creds.status is Status.FAIL
    assert "copy .env.example" in creds.detail
    assert exit_code(checks) == 1


def test_env_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADOVATE_ENV", "live")
    creds = _by_name(run_checks(_options(tmp_path)))["credentials"]
    assert creds.status is Status.FAIL
    assert "must match" in creds.detail


def test_bad_config_fails_but_other_checks_still_run(tmp_path: Path) -> None:
    bad = tmp_path / "ace.yaml"
    bad.write_text("environment: demo\n", encoding="utf-8")
    checks = run_checks(_options(tmp_path, config_path=bad))
    by_name = _by_name(checks)
    assert by_name["config"].status is Status.FAIL
    assert by_name["credentials"].status is Status.OK
    assert exit_code(checks) == 1


@pytest.mark.parametrize(
    ("offset", "status"), [(0.249, Status.OK), (-0.249, Status.OK), (0.3, Status.FAIL)]
)
def test_clock_drift_limit(tmp_path: Path, offset: float, status: Status) -> None:
    opts = _options(tmp_path, drift=lambda server: _drift(offset))
    assert _by_name(run_checks(opts))["clock drift"].status is status


def test_unreachable_ntp_warns(tmp_path: Path) -> None:
    def drift(server: str) -> DriftSample:
        raise NtpError("timed out")

    check = _by_name(run_checks(_options(tmp_path, drift=drift)))["clock drift"]
    assert check.status is Status.WARN


def test_offline_skips_clock(tmp_path: Path) -> None:
    check = _by_name(run_checks(_options(tmp_path, check_clock=False)))["clock drift"]
    assert check.status is Status.SKIP


def test_windows_checks(tmp_path: Path) -> None:
    def run(args: Sequence[str]) -> CommandResult:
        if args[0] == "powercfg":
            if args[-1] == windows.HIBERNATE_IDLE:
                return CommandResult(0, POWERCFG.replace("0x00000708", "0x00000000"))
            return CommandResult(0, POWERCFG)
        if args[0] == "w32tm":
            return CommandResult(0, W32TM)
        return CommandResult(0, REG)

    by_name = _by_name(run_checks(_options(tmp_path, platform="win32", run=run)))
    assert by_name["sleep on AC"].status is Status.FAIL
    assert "30 min" in by_name["sleep on AC"].detail
    assert by_name["hibernate on AC"].status is Status.OK
    assert by_name["time sync"].status is Status.OK
    assert by_name["active hours"].detail == "08:00-17:00 local"


def test_windows_time_service_stopped(tmp_path: Path) -> None:
    def run(args: Sequence[str]) -> CommandResult:
        return CommandResult(1, "The service has not been started.")

    by_name = _by_name(run_checks(_options(tmp_path, platform="win32", run=run)))
    assert by_name["time sync"].status is Status.FAIL
    assert by_name["sleep on AC"].status is Status.WARN


def test_report_never_prints_secret_values(tmp_path: Path) -> None:
    report = format_report(run_checks(_options(tmp_path)))
    for key, value in VALUES.items():
        if key != "TRADOVATE_ENV":
            assert value not in report
    assert "[OK  ] config" in report

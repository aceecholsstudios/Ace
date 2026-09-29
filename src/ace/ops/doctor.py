"""``ace doctor``: check that this machine and its config are ready to run Ace.

Each check returns a :class:`Check`; none of them raise. The command fails
(non-zero exit) only on FAIL results. WARN marks something to fix before going
live that doesn't block development.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ace.core.config import AceConfig, ConfigError, FeesConfig, load_config, load_fees
from ace.core.secrets import SecretsError, load_secrets
from ace.ops import windows
from ace.ops.clock import DriftSample, NtpError, measure_drift

MAX_CLOCK_DRIFT_SEC = 0.250


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    config_path: Path = Path("config/ace.yaml")
    fees_path: Path = Path("config/fees.yaml")
    env_file: Path = Path(".env")
    data_dir: Path = Path("data")
    check_clock: bool = True
    ntp_server: str = "pool.ntp.org"
    platform: str = sys.platform
    run: windows.Runner = windows.run_command
    drift: Callable[[str], DriftSample] = field(default=measure_drift)


def run_checks(opts: DoctorOptions) -> list[Check]:
    return list(_iter_checks(opts))


def _iter_checks(opts: DoctorOptions) -> Iterator[Check]:
    yield _check_python()

    config: AceConfig | None = None
    try:
        config = load_config(opts.config_path)
    except ConfigError as exc:
        yield Check("config", Status.FAIL, str(exc))
    else:
        yield Check(
            "config",
            Status.OK if config.environment == "demo" else Status.WARN,
            f"{opts.config_path} valid; environment={config.environment}, "
            f"symbols={','.join(config.symbols)}",
        )

    try:
        fees = load_fees(opts.fees_path)
    except ConfigError as exc:
        yield Check("fees", Status.FAIL, str(exc))
    else:
        yield _check_fees(fees, config, opts.fees_path)

    yield _check_secrets(opts.env_file, config)
    yield _check_data_dir(opts.data_dir)

    if opts.check_clock:
        yield _check_clock(opts.drift, opts.ntp_server)
    else:
        yield Check("clock drift", Status.SKIP, "skipped (--offline)")

    yield from _windows_checks(opts)


def _check_python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    ok = sys.version_info[:2] == (3, 12)
    return Check("python", Status.OK if ok else Status.WARN, f"{version} (target 3.12)")


def _check_fees(fees: FeesConfig, config: AceConfig | None, path: Path) -> Check:
    if config is not None and (missing := set(config.symbols) - set(fees.per_side)):
        return Check("fees", Status.FAIL, f"{path} is missing symbols: {sorted(missing)}")
    per_symbol = ", ".join(
        f"{root} ${fees.round_trip_usd(root):.2f} ({fees.round_trip_ticks(root):.1f} ticks)"
        for root in fees.per_side
    )
    detail = f"plan={fees.plan}; round trip {per_symbol}"
    if not fees.verified:
        return Check("fees", Status.WARN, f"{detail}. Unverified: copy from your statement")
    return Check("fees", Status.OK, detail)


def _check_secrets(env_file: Path, config: AceConfig | None) -> Check:
    source = str(env_file) if env_file.is_file() else "process environment"
    try:
        secrets = load_secrets(env_file if env_file.is_file() else None)
    except SecretsError as exc:
        hint = "" if env_file.is_file() else f" ({env_file} not found; copy .env.example)"
        return Check("credentials", Status.FAIL, f"{exc}{hint}")
    if config is not None and secrets.tradovate_env != config.environment:
        return Check(
            "credentials",
            Status.FAIL,
            f"TRADOVATE_ENV={secrets.tradovate_env} but config environment="
            f"{config.environment}; they must match",
        )
    return Check("credentials", Status.OK, f"all set from {source}")


def _check_data_dir(data_dir: Path) -> Check:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data_dir):
            pass
    except OSError as exc:
        return Check("data dir", Status.FAIL, f"{data_dir} not writable: {exc}")
    return Check("data dir", Status.OK, f"{data_dir.resolve()} writable")


def _check_clock(drift: Callable[[str], DriftSample], server: str) -> Check:
    try:
        sample = drift(server)
    except NtpError as exc:
        return Check("clock drift", Status.WARN, f"could not measure: {exc}")
    ms = sample.offset_sec * 1000
    detail = f"{ms:+.0f} ms vs {sample.server} (limit {MAX_CLOCK_DRIFT_SEC * 1000:.0f} ms)"
    ok = abs(sample.offset_sec) < MAX_CLOCK_DRIFT_SEC
    return Check("clock drift", Status.OK if ok else Status.FAIL, detail)


def _windows_checks(opts: DoctorOptions) -> Iterator[Check]:
    names = ("sleep on AC", "hibernate on AC", "time sync", "active hours")
    if opts.platform != "win32":
        for name in names:
            yield Check(name, Status.SKIP, "Windows only")
        return

    for name, setting in (
        ("sleep on AC", windows.STANDBY_IDLE),
        ("hibernate on AC", windows.HIBERNATE_IDLE),
    ):
        seconds = windows.ac_timeout_seconds(setting, opts.run)
        if seconds is None:
            yield Check(name, Status.WARN, "could not read power setting")
        elif seconds == 0:
            yield Check(name, Status.OK, "disabled")
        else:
            yield Check(name, Status.FAIL, f"after {seconds // 60} min; set to Never")

    source = windows.time_sync_source(opts.run)
    if source is None:
        yield Check("time sync", Status.FAIL, "Windows Time service not running")
    elif not windows.is_synced_source(source):
        yield Check("time sync", Status.FAIL, f"source is {source!r}; enable internet time")
    else:
        yield Check("time sync", Status.OK, f"source {source}")

    hours = windows.active_hours(opts.run)
    if hours is None:
        yield Check("active hours", Status.WARN, "not set; set them to cover the trading windows")
    else:
        yield Check("active hours", Status.OK, f"{hours[0]:02d}:00-{hours[1]:02d}:00 local")


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"[{c.status:<4}] {c.name:<{width}}  {c.detail}" for c in checks]
    counts = {s: sum(c.status is s for c in checks) for s in Status}
    lines.append("")
    lines.append(", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status is Status.FAIL for c in checks) else 0

"""Windows host settings that ``ace doctor`` checks (design doc Section 12).

Each check shells out to a stock Windows tool and parses its output. Parsing is
split from running so the parsers can be unit-tested on any OS. Tool output is
localized on non-English Windows; anything we can't parse is reported as
unknown rather than guessed.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

Runner = Callable[[Sequence[str]], "CommandResult"]

# powercfg GUIDs (stable across Windows versions and locales).
SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
STANDBY_IDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
HIBERNATE_IDLE = "9d7815a6-7ee4-497e-8888-515a05f02364"

_AC_INDEX = re.compile(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)")
_W32TM_SOURCE = re.compile(r"^Source:\s*(.+?)\s*$", re.MULTILINE)
_REG_DWORD = re.compile(r"^\s*(\w+)\s+REG_DWORD\s+0x([0-9a-fA-F]+)\s*$", re.MULTILINE)
_UNSYNCED_SOURCES = {"local cmos clock", "free-running system clock"}


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str


def run_command(args: Sequence[str]) -> CommandResult:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument lists, no shell
            list(args), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(returncode=-1, stdout=str(exc))
    return CommandResult(returncode=proc.returncode, stdout=proc.stdout)


def parse_powercfg_ac_seconds(output: str) -> int | None:
    """Return the AC timeout in seconds from ``powercfg /query``; 0 means never."""
    match = _AC_INDEX.search(output)
    return int(match.group(1), 16) if match else None


def parse_w32tm_source(output: str) -> str | None:
    match = _W32TM_SOURCE.search(output)
    return match.group(1) if match else None


def is_synced_source(source: str) -> bool:
    return source.strip().lower() not in _UNSYNCED_SOURCES


def parse_reg_dwords(output: str) -> dict[str, int]:
    return {name: int(value, 16) for name, value in _REG_DWORD.findall(output)}


def ac_timeout_seconds(setting: str, run: Runner = run_command) -> int | None:
    result = run(["powercfg", "/query", "SCHEME_CURRENT", SUB_SLEEP, setting])
    if result.returncode != 0:
        return None
    return parse_powercfg_ac_seconds(result.stdout)


def time_sync_source(run: Runner = run_command) -> str | None:
    """The configured time source, or None if the Windows Time service isn't running."""
    result = run(["w32tm", "/query", "/status"])
    if result.returncode != 0:
        return None
    return parse_w32tm_source(result.stdout)


def active_hours(run: Runner = run_command) -> tuple[int, int] | None:
    """Windows Update active hours as (start hour, end hour), local time."""
    result = run(
        [
            "reg",
            "query",
            r"HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings",
        ]
    )
    if result.returncode != 0:
        return None
    values = parse_reg_dwords(result.stdout)
    try:
        return values["ActiveHoursStart"], values["ActiveHoursEnd"]
    except KeyError:
        return None

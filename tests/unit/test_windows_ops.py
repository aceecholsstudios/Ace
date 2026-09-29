from __future__ import annotations

from collections.abc import Sequence

from ace.ops import windows
from ace.ops.windows import CommandResult

POWERCFG = """\
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  Subgroup GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Sleep)
    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0xffffffff
      Possible Settings increment: 0x00000001
      Possible Settings units: Seconds
    Current AC Power Setting Index: 0x00000708
    Current DC Power Setting Index: 0x00000384
"""

W32TM = """\
Leap Indicator: 0(no warning)
Stratum: 4 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Last Successful Sync Time: 9/29/2026 9:12:03 AM
Source: time.windows.com,0x9
Poll Interval: 10 (1024s)
"""

REG = r"""
HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings
    ActiveHoursEnd    REG_DWORD    0x11
    ActiveHoursStart    REG_DWORD    0x8
    SmartActiveHoursState    REG_DWORD    0x0
"""


def test_parse_powercfg_reads_ac_not_dc() -> None:
    assert windows.parse_powercfg_ac_seconds(POWERCFG) == 1800
    assert windows.parse_powercfg_ac_seconds("garbled") is None


def test_parse_w32tm_source() -> None:
    assert windows.parse_w32tm_source(W32TM) == "time.windows.com,0x9"
    assert windows.is_synced_source("time.windows.com,0x9")
    assert not windows.is_synced_source("Local CMOS Clock")
    assert not windows.is_synced_source("Free-running System Clock")


def test_active_hours() -> None:
    def run(args: Sequence[str]) -> CommandResult:
        assert args[:2] == ["reg", "query"]
        return CommandResult(0, REG)

    assert windows.active_hours(run) == (8, 17)


def test_failed_commands_return_none() -> None:
    def run(args: Sequence[str]) -> CommandResult:
        return CommandResult(1, "")

    assert windows.ac_timeout_seconds(windows.STANDBY_IDLE, run) is None
    assert windows.time_sync_source(run) is None
    assert windows.active_hours(run) is None

from __future__ import annotations

import struct

import pytest

from ace.ops.clock import NTP_EPOCH_OFFSET, NtpError, parse_reply


def _ntp(ts: float) -> tuple[int, int]:
    seconds = int(ts)
    return seconds + NTP_EPOCH_OFFSET, int((ts - seconds) * 2**32)


def _reply(rx: float, tx: float, *, mode: int = 4, stratum: int = 2) -> bytes:
    words = [0, 0, 0, 0, 0, 0, 0, *_ntp(rx), *_ntp(tx)]
    return struct.pack("!B B B b 11I", (4 << 3) | mode, stratum, 0, 0, *words)


def test_local_clock_slow_gives_positive_offset() -> None:
    # Local clock 0.3 s behind the server; 20 ms each way; 1 ms server processing.
    t_sent = 1_000.000
    rx = t_sent + 0.3 + 0.020
    tx = rx + 0.001
    t_received = tx - 0.3 + 0.020
    sample = parse_reply(_reply(rx, tx), t_sent, t_received, "ntp.test")
    assert sample.offset_sec == pytest.approx(0.3, abs=1e-6)
    assert sample.round_trip_sec == pytest.approx(0.040, abs=1e-6)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (b"\x24" + b"\0" * 10, "short reply"),
        (_reply(1.0, 1.0, mode=3), "mode 3"),
        (_reply(1.0, 1.0, stratum=0), "kiss-of-death"),
    ],
)
def test_bad_replies(reply: bytes, message: str) -> None:
    with pytest.raises(NtpError, match=message):
        parse_reply(reply, 0.0, 0.0, "ntp.test")

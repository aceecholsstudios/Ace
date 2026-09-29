"""Clock drift against an NTP server, via a minimal SNTP (RFC 4330) query.

Bar boundaries and the signal lab's forward returns depend on accurate time,
so ``ace doctor`` requires drift under 250 ms (design doc Section 12).
"""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

NTP_EPOCH_OFFSET = 2_208_988_800  # seconds from 1900-01-01 to 1970-01-01
_PACKET = struct.Struct("!B B B b 11I")
_REQUEST = b"\x23" + 47 * b"\0"  # LI=0, VN=4, Mode=3 (client)


class NtpError(Exception):
    """The NTP server could not be reached or sent a bad reply."""


@dataclass(frozen=True, slots=True)
class DriftSample:
    server: str
    offset_sec: float
    """Server clock minus local clock. Positive means the local clock is slow."""
    round_trip_sec: float


def _from_ntp(seconds: int, fraction: int) -> float:
    return seconds - NTP_EPOCH_OFFSET + fraction / 2**32


def parse_reply(reply: bytes, t_sent: float, t_received: float, server: str) -> DriftSample:
    """Compute offset and delay from a server reply and local send/receive times."""
    if len(reply) < _PACKET.size:
        raise NtpError(f"short reply from {server} ({len(reply)} bytes)")
    fields = _PACKET.unpack_from(reply)
    li_vn_mode, stratum = fields[0], fields[1]
    mode = li_vn_mode & 0x7
    if mode != 4:  # SNTP server mode
        raise NtpError(f"{server} replied with mode {mode}, expected 4 (server)")
    if stratum == 0:
        raise NtpError(f"{server} sent a kiss-of-death reply")
    words = fields[4:]
    # words: root delay, root dispersion, ref id, ref ts (2), orig ts (2), rx ts (2), tx ts (2)
    t_server_rx = _from_ntp(words[7], words[8])
    t_server_tx = _from_ntp(words[9], words[10])
    offset = ((t_server_rx - t_sent) + (t_server_tx - t_received)) / 2
    delay = (t_received - t_sent) - (t_server_tx - t_server_rx)
    return DriftSample(server=server, offset_sec=offset, round_trip_sec=delay)


def measure_drift(
    server: str = "pool.ntp.org",
    timeout_sec: float = 2.0,
    clock: Callable[[], float] = time.time,
) -> DriftSample:
    """Query ``server`` once over UDP and return the local clock's offset."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_sec)
            t_sent = clock()
            sock.sendto(_REQUEST, (server, 123))
            reply, _ = sock.recvfrom(1024)
            t_received = clock()
    except OSError as exc:
        raise NtpError(f"cannot query {server}: {exc}") from exc
    return parse_reply(reply, t_sent, t_received, server)

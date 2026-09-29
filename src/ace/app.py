"""Command-line entry point: ``ace run | doctor | report``.

Only ``doctor`` works as of milestone M0. ``run`` wires the engine and the
dashboard together once they exist (M1-M6); ``report`` arrives with M6.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ace import __version__
from ace.ops.doctor import DoctorOptions, exit_code, format_report, run_checks

EXIT_NOT_IMPLEMENTED = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ace", description="Ace order-flow scalping bot")
    parser.add_argument("--version", action="version", version=f"ace {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check config, credentials and host settings")
    doctor.add_argument("--config", type=Path, default=Path("config/ace.yaml"))
    doctor.add_argument("--fees", type=Path, default=Path("config/fees.yaml"))
    doctor.add_argument("--env-file", type=Path, default=Path(".env"))
    doctor.add_argument("--data-dir", type=Path, default=Path("data"))
    doctor.add_argument("--ntp-server", default="pool.ntp.org")
    doctor.add_argument("--offline", action="store_true", help="skip the NTP clock check")

    sub.add_parser("run", help="start the engine and dashboard (not yet implemented)")
    sub.add_parser("report", help="generate reports (not yet implemented)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "doctor":
        checks = run_checks(
            DoctorOptions(
                config_path=args.config,
                fees_path=args.fees,
                env_file=args.env_file,
                data_dir=args.data_dir,
                check_clock=not args.offline,
                ntp_server=args.ntp_server,
            )
        )
        print(format_report(checks))
        return exit_code(checks)

    print(f"ace {args.command}: not implemented yet", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())

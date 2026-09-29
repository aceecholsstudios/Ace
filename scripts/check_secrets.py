"""Pre-commit hook: block commits that contain credentials (design doc Section 6.3).

Fails when a staged file is a ``.env`` file, or assigns a non-empty value to
one of Ace's credential variables. ``.env.example`` passes because it lists
keys with empty values.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

SECRET_KEYS = (
    "TRADOVATE_USERNAME",
    "TRADOVATE_PASSWORD",
    "TRADOVATE_CID",
    "TRADOVATE_SECRET",
    "DATABENTO_API_KEY",
)
# [ \t] rather than \s so an empty "KEY=" can't match the next line as its value.
_ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?"
    r"(?P<key>" + "|".join(SECRET_KEYS) + r")"
    r"[ \t]*[=:][ \t]*(?P<value>\S.*)$",
    re.MULTILINE,
)
_PLACEHOLDERS = {"...", '""', "''", "<redacted>"}


def _is_env_file(path: Path) -> bool:
    return path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example")


def find_problems(path: Path, text: str) -> list[str]:
    if _is_env_file(path):
        return [f"{path}: .env files must never be committed"]
    problems = []
    for match in _ASSIGNMENT.finditer(text):
        value = match.group("value").strip()
        if value not in _PLACEHOLDERS:
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{path}:{line}: {match.group('key')} has a value")
    return problems


def main(argv: Sequence[str]) -> int:
    problems: list[str] = []
    for name in argv:
        path = Path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        problems.extend(find_problems(path, text))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

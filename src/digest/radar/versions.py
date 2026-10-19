"""Loose version parsing for release tags and declared versions."""

import re

NUMBERS = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")

Version = tuple[int, int, int]


def parse_version(text: str | None) -> Version | None:
    """First x[.y[.z]] in a tag or version: "v1.2.3", "lodash@4.17.21", "1.2" -> (1, 2, 0)."""
    match = NUMBERS.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0))


def major_line(version: Version) -> tuple[int, ...]:
    """Releases that may break each other: 2.x is one line; for 0.x each minor is one."""
    return (version[0],) if version[0] > 0 else (0, version[1])

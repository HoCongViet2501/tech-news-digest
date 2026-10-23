"""Lenient version handling: enough to compare majors across npm, PyPI and Go tags."""

import re

NUMERIC = re.compile(r"\d+(?:\.\d+)*")


def numeric(version: str | None) -> tuple[int, int, int] | None:
    """First dotted number in the string, padded to (major, minor, patch).

    Works for "v1.2.3", "4.17.15", "lodash@4.17.21", "release-2.0"; None if no digits.
    """
    if not version:
        return None
    match = NUMERIC.search(version)
    if not match:
        return None
    parts = [int(p) for p in match.group().split(".")[:3]]
    major, minor, patch = (parts + [0, 0])[:3]
    return major, minor, patch

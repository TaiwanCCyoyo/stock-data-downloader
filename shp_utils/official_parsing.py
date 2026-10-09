"""Defensive parsers for official TWSE/TPEx field values.

These are pure and import nothing beyond the standard library, so a consumer
re-parsing a stored response can use them without pulling in the HTTP transport
that lives in :mod:`shp_utils.official_api` -- which installs an OS trust store
at import time. ``official_api`` re-exports them, so every existing import site
keeps working.
"""

from __future__ import annotations

from typing import Any


def parse_roc_compact_date(value: str) -> str | None:
    """Parse a 7-digit ROC-year date with no separators, e.g. ``"1150722"`` ->
    ``"2026-07-22"``. Returns None for anything else."""
    text = str(value).strip()
    if len(text) != 7 or not text.isdigit():
        return None
    year = int(text[:3]) + 1911
    month = int(text[3:5])
    day = int(text[5:7])
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_number(value: Any) -> float | None:
    """Parse a numeric field that may contain commas, surrounding whitespace,
    or a ``---`` no-trade sentinel; returns None for any non-numeric value."""
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    """Parse an integer-valued field (share counts, transaction counts) using
    the same defensive rules as :func:`parse_number`."""
    number = parse_number(value)
    if number is None:
        return None
    return int(number)

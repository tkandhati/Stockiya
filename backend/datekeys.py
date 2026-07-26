"""Shared helper: extract a trade/run date embedded in a data filename.

One place for the filename-date logic used by the delivery loader and the
archiver (previously duplicated in both). Tries ISO (YYYY-MM-DD), then the NSE
DDMMYYYY form, then DDMMYY.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional


def date_from_filename(name: str) -> Optional[date]:
    """Date embedded in `name`, or None. ISO first, then DDMMYYYY, then DDMMYY."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", name)          # ISO YYYY-MM-DD
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", name)             # DDMMYYYY (NSE MTO)
    if m:
        s = m.group(1)
        try:
            return date(int(s[4:8]), int(s[2:4]), int(s[0:2]))
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", name)             # DDMMYY
    if m:
        s = m.group(1)
        try:
            return date(2000 + int(s[4:6]), int(s[2:4]), int(s[0:2]))
        except ValueError:
            pass
    return None


def iso_from_filename(name: str) -> Optional[str]:
    """`date_from_filename` as an ISO string, or None."""
    d = date_from_filename(name)
    return d.isoformat() if d else None

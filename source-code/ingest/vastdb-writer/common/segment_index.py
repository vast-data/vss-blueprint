"""Segment identity helpers for VastDB writer idempotency."""
from __future__ import annotations

import hashlib


def pk_for_source(source: str) -> str:
    return hashlib.md5(source.encode()).hexdigest()

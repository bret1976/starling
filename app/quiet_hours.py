"""Quiet Hours Pack — hold auto review-request SMS/email during a local window.

Idea inspiration (no code copied): amayer1983/docksentry quiet_hours
(MIT, https://github.com/amayer1983/docksentry) — drop auto-notifications
inside QUIET_HOURS_START/END rather than queueing stale pings.

Original Starling Python: wraps send_review_request delivery only. Manual
link-copy channel still works. No new UI/screens — env + health marker +
API response fields only.
"""
from __future__ import annotations

import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

PACK = "quiet-hours-v1"


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        parts = value.strip().split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            return None
        return time(h, m)
    except (ValueError, AttributeError):
        return None


def configured() -> dict[str, str]:
    start = (os.environ.get("QUIET_HOURS_START") or "").strip()
    end = (os.environ.get("QUIET_HOURS_END") or "").strip()
    tz = (os.environ.get("QUIET_HOURS_TZ") or os.environ.get("TZ") or "America/Los_Angeles").strip()
    return {"start": start, "end": end, "tz": tz}


def is_quiet_now(now: datetime | None = None) -> bool:
    """True when local time is inside the configured quiet window.

    Empty start or end → feature off → False.
    Window may wrap midnight (e.g. 22:00–07:00).
    """
    cfg = configured()
    start = _parse_hhmm(cfg["start"])
    end = _parse_hhmm(cfg["end"])
    if start is None or end is None or start == end:
        return False
    try:
        tz = ZoneInfo(cfg["tz"])
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    current = (now or datetime.now(tz)).astimezone(tz).time()
    if start < end:
        return start <= current < end
    return current >= start or current < end


def guard_auto_delivery(channel: str) -> dict[str, Any] | None:
    """If quiet hours are active and channel is sms/email, return a skip result.

    Returns None when delivery should proceed.
    """
    if channel not in {"sms", "email"}:
        return None
    if not is_quiet_now():
        return None
    cfg = configured()
    return {
        "ok": False,
        "skipped": True,
        "quiet_hours": True,
        "pack": PACK,
        "channel": channel,
        "error": (
            f"Quiet hours active ({cfg['start']}–{cfg['end']} {cfg['tz']}). "
            "Auto SMS/email held; link still available to copy."
        ),
        "window": cfg,
    }


def summary() -> dict[str, Any]:
    cfg = configured()
    enabled = bool(_parse_hhmm(cfg["start"]) and _parse_hhmm(cfg["end"]) and cfg["start"] != cfg["end"])
    return {
        "pack": PACK,
        "enabled": enabled,
        "active_now": is_quiet_now() if enabled else False,
        "window": cfg if enabled else None,
    }

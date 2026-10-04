"""Ask Cooldown Pack — hold duplicate auto review-request SMS/email.

Idea inspiration (no code copied):
- Show HN SafeAgent exactly-once execution guard (request_id receipts)
- pratiksnaik5/privacy-timeguard (MIT) time-window allow/deny
- Reddit n8n/Make patterns: dedupe by contact + cooldown before re-SMS

Original Starling Python: before auto SMS/email delivery, look up a recent
successful review_request for the same location+customer. Soft kill-switch
ASK_COOLDOWN=0. Link channel unchanged. No new UI/screens.
"""
from __future__ import annotations

import os
import time
from typing import Any

from app.db import connect, row

PACK = "ask-cooldown-v1"

# Process-local counters (reset on restart; ops summary only).
_stats = {"checked": 0, "held": 0, "allowed": 0}


def enabled() -> bool:
    raw = (os.environ.get("ASK_COOLDOWN") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def cooldown_hours() -> float:
    try:
        h = float(os.environ.get("ASK_COOLDOWN_HOURS") or "72")
    except ValueError:
        h = 72.0
    return max(0.0, min(h, 24 * 30))


def recent_sent(lid: int, customer_id: int, now: float | None = None) -> dict[str, Any] | None:
    """Return the most recent successful SMS/email ask inside the cooldown window."""
    if not enabled():
        return None
    hours = cooldown_hours()
    if hours <= 0:
        return None
    cutoff = (now if now is not None else time.time()) - hours * 3600.0
    con = connect()
    try:
        found = row(
            con.execute(
                """SELECT id, token, channel, status, sent_at
                   FROM review_requests
                   WHERE location_id = ? AND customer_id = ?
                     AND channel IN ('sms', 'email')
                     AND status = 'sent'
                     AND sent_at >= ?
                   ORDER BY sent_at DESC
                   LIMIT 1""",
                (lid, customer_id, cutoff),
            ).fetchone()
        )
    finally:
        con.close()
    return found


def guard_auto_delivery(
    lid: int,
    customer_id: int,
    channel: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """If a recent sent ask exists for sms/email, return a skip result.

    Returns None when delivery should proceed. Link channel always proceeds.
    """
    _stats["checked"] += 1
    if channel not in {"sms", "email"}:
        _stats["allowed"] += 1
        return None
    if not enabled():
        _stats["allowed"] += 1
        return None
    prev = recent_sent(lid, customer_id, now=now)
    if not prev:
        _stats["allowed"] += 1
        return None
    hours = cooldown_hours()
    age_h = max(0.0, ((now if now is not None else time.time()) - float(prev["sent_at"])) / 3600.0)
    _stats["held"] += 1
    return {
        "ok": False,
        "skipped": True,
        "ask_cooldown": True,
        "pack": PACK,
        "channel": channel,
        "error": (
            f"Ask cooldown active ({hours:g}h). Customer already received a "
            f"{prev['channel']} review request ~{age_h:.1f}h ago. Auto SMS/email "
            "held; link still available to copy."
        ),
        "previous": {
            "id": prev["id"],
            "token": prev["token"],
            "channel": prev["channel"],
            "sent_at": prev["sent_at"],
            "status": prev["status"],
        },
        "cooldown_hours": hours,
        "age_hours": round(age_h, 3),
    }


def summary() -> dict[str, Any]:
    return {
        "pack": PACK,
        "enabled": enabled(),
        "cooldown_hours": cooldown_hours() if enabled() else None,
        "process": dict(_stats),
    }

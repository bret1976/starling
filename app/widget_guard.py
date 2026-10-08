"""Widget Guard Pack (widget-guard-v1) — cap paid Grok replies on the public web-chat widget.

`POST /api/widget/chat` is public (no login) and, before this pack, called the paid
xAI Grok API for EVERY message, with no limit and the full message text as prompt.
A bot (or one stuck browser tab) could run up the AI bill and flood the inbox meter.

Now, before calling Grok:
  - per-visitor budget (salted SHA-256 of the client IP, truncated; raw IPs are never
    stored or exposed): WIDGET_GUARD_CLIENT_AI_PER_WINDOW (8) per WIDGET_GUARD_WINDOW_SEC (600)
  - per-location budget: WIDGET_GUARD_LOCATION_AI_PER_HOUR (60)
  - global budget: WIDGET_GUARD_GLOBAL_AI_PER_HOUR (300)
  - prompt cap: only the first WIDGET_GUARD_PROMPT_CHARS (2000) characters go to Grok
    (the full message is still saved to the inbox exactly as before).
Over budget -> Grok is skipped and the visitor gets the SAME fallback reply the endpoint
already used when Grok was unavailable ("Thanks — <business> received this and will
reply from the inbox."). The message is still saved; the owner still sees it in the
inbox. No new UI, no new copy, response shape unchanged.

Never sends anything, never spends anything; it only reduces paid AI calls.
Kill switch: WIDGET_GUARD=0. Memory-only counters (reset on deploy).

Pattern credits (ideas only, no code copied):
  - laurentS/slowapi (MIT) per-key limits for Starlette/FastAPI
  - animir/node-rate-limiter-flexible (ISC) keyed + global limiter pair
  - express-rate-limit/express-rate-limit (MIT) sliding window per client
  - Starling's own ask-cooldown-v1 / quiet-hours-v1 conventions
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import deque
from typing import Any

PACK = "widget-guard-v1"

_SALT = secrets.token_hex(16)  # per-process; hashes are never comparable across deploys
_lock = threading.Lock()
_client_hits: dict[str, deque[float]] = {}
_location_hits: dict[int, deque[float]] = {}
_global_hits: deque[float] = deque()
_stats: dict[str, Any] = {
    "checked": 0,
    "ai_allowed": 0,
    "held_client": 0,
    "held_location": 0,
    "held_global": 0,
    "prompts_trimmed": 0,
    "last_held_at": None,
    "since": time.time(),
}


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(str(os.environ.get(name) or default).strip())
    except ValueError:
        v = default
    return max(lo, min(v, hi))


def enabled() -> bool:
    raw = (os.environ.get("WIDGET_GUARD") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def window_sec() -> int:
    return _int_env("WIDGET_GUARD_WINDOW_SEC", 600, 30, 86400)


def client_limit() -> int:
    return _int_env("WIDGET_GUARD_CLIENT_AI_PER_WINDOW", 8, 1, 10000)


def location_limit() -> int:
    return _int_env("WIDGET_GUARD_LOCATION_AI_PER_HOUR", 60, 1, 100000)


def global_limit() -> int:
    return _int_env("WIDGET_GUARD_GLOBAL_AI_PER_HOUR", 300, 1, 1000000)


def prompt_chars() -> int:
    return _int_env("WIDGET_GUARD_PROMPT_CHARS", 2000, 200, 20000)


def client_key(headers: Any, fallback_host: str | None) -> str | None:
    """Salted, truncated hash of the client IP. Railway's edge sets X-Real-IP."""
    ip = ""
    try:
        ip = (headers.get("x-real-ip") or "").strip()
        if not ip:
            ip = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    except Exception:
        ip = ""
    if not ip:
        ip = (fallback_host or "").strip()
    if not ip:
        return None
    return hashlib.sha256(f"{_SALT}:{ip}".encode()).hexdigest()[:20]


def _prune(dq: deque[float], cutoff: float) -> None:
    while dq and dq[0] < cutoff:
        dq.popleft()


def check_ai(location_id: int, key: str | None, now: float | None = None) -> dict[str, Any]:
    """Decide whether this widget message may use a paid Grok reply.

    Returns {"allow": bool, "reason": str}. Records the hit when allowed.
    Unknown client (no IP) is never lumped into a shared bucket — only the
    location and global budgets apply.
    """
    t = time.time() if now is None else now
    with _lock:
        _stats["checked"] += 1
        if not enabled():
            _stats["ai_allowed"] += 1
            return {"allow": True, "reason": "disabled"}
        hour_cut = t - 3600.0
        win_cut = t - float(window_sec())
        _prune(_global_hits, hour_cut)
        if len(_global_hits) >= global_limit():
            _stats["held_global"] += 1
            _stats["last_held_at"] = t
            return {"allow": False, "reason": "global_limit"}
        loc = _location_hits.setdefault(int(location_id), deque())
        _prune(loc, hour_cut)
        if len(loc) >= location_limit():
            _stats["held_location"] += 1
            _stats["last_held_at"] = t
            return {"allow": False, "reason": "location_limit"}
        cli = None
        if key:
            cli = _client_hits.setdefault(key, deque())
            _prune(cli, win_cut)
            if len(cli) >= client_limit():
                _stats["held_client"] += 1
                _stats["last_held_at"] = t
                return {"allow": False, "reason": "client_limit"}
        _global_hits.append(t)
        loc.append(t)
        if cli is not None:
            cli.append(t)
        # keep memory bounded: drop idle client buckets
        if len(_client_hits) > 5000:
            for k in [k for k, v in _client_hits.items() if not v or v[-1] < win_cut][:2500]:
                _client_hits.pop(k, None)
        _stats["ai_allowed"] += 1
        return {"allow": True, "reason": "ok"}


def ai_prompt(text: str) -> str:
    """Trim what goes to Grok (stored message is untouched)."""
    if not enabled():
        return text
    cap = prompt_chars()
    if len(text) <= cap:
        return text
    with _lock:
        _stats["prompts_trimmed"] += 1
    return text[:cap]


def summary() -> dict[str, Any]:
    now = time.time()
    with _lock:
        _prune(_global_hits, now - 3600.0)
        held = _stats["held_client"] + _stats["held_location"] + _stats["held_global"]
        return {
            "pack": PACK,
            "enabled": enabled(),
            "limits": {
                "client_ai_per_window": client_limit(),
                "window_sec": window_sec(),
                "location_ai_per_hour": location_limit(),
                "global_ai_per_hour": global_limit(),
                "prompt_chars": prompt_chars(),
            },
            "process": {
                "checked": _stats["checked"],
                "ai_allowed": _stats["ai_allowed"],
                "held_total": held,
                "held_client": _stats["held_client"],
                "held_location": _stats["held_location"],
                "held_global": _stats["held_global"],
                "prompts_trimmed": _stats["prompts_trimmed"],
                "ai_calls_last_hour": len(_global_hits),
                "tracked_clients": len(_client_hits),
                "last_held_at": _stats["last_held_at"],
                "since": _stats["since"],
            },
        }


def reset_for_test() -> None:
    with _lock:
        _client_hits.clear()
        _location_hits.clear()
        _global_hits.clear()
        for k in ("checked", "ai_allowed", "held_client", "held_location", "held_global", "prompts_trimmed"):
            _stats[k] = 0
        _stats["last_held_at"] = None

"""Login Throttle Pack (login-throttle-v1) — stop brute-force on /api/auth/login.

`POST /api/auth/login` (and signup) are public and, before this pack, accepted
unlimited attempts with no per-IP or per-email lockout. A bot can spray passwords
against known emails and burn CPU on PBKDF2 hashes (~120k iterations).

Now, before the password check:
  - per-client attempt budget (salted SHA-256 of client IP, truncated; raw IPs
    never stored): LOGIN_THROTTLE_CLIENT_PER_WINDOW (20) per
    LOGIN_THROTTLE_WINDOW_SEC (900)
  - per-email failure lockout: LOGIN_THROTTLE_EMAIL_FAILS (8) failures inside the
    same window locks that email (normalized) until the oldest failure ages out
  - global attempt budget: LOGIN_THROTTLE_GLOBAL_PER_HOUR (400)
Over budget -> HTTP 429 with Retry-After; password is NOT checked (no extra
hashing cost). Successful login clears that email's failure window.
Signup shares the per-client + global attempt budgets (account-spam speed bump)
but does not use the email-failure lock (signup already 409s on duplicate email).

Never changes cookies, UI, static files, or response shape on allow.
Kill switch: LOGIN_THROTTLE=0. Memory-only counters (reset on deploy).

Pattern credits (ideas only, no code copied):
  - jazzband/django-axes (MIT) failed-login lockout by username/IP
  - laurentS/slowapi (MIT) per-key limits for Starlette/FastAPI
  - express-rate-limit/express-rate-limit (MIT) sliding window + Retry-After
  - animir/node-rate-limiter-flexible (ISC) keyed + global limiter pair
  - Starling's own widget-guard-v1 conventions
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import deque
from typing import Any

PACK = "login-throttle-v1"

_SALT = secrets.token_hex(16)  # per-process; hashes never comparable across deploys
_lock = threading.Lock()
_client_hits: dict[str, deque[float]] = {}
_email_fails: dict[str, deque[float]] = {}
_global_hits: deque[float] = deque()
_stats: dict[str, Any] = {
    "checked": 0,
    "allowed": 0,
    "held_client": 0,
    "held_email": 0,
    "held_global": 0,
    "failures_recorded": 0,
    "success_clears": 0,
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
    raw = (os.environ.get("LOGIN_THROTTLE") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def window_sec() -> int:
    return _int_env("LOGIN_THROTTLE_WINDOW_SEC", 900, 30, 86400)


def client_limit() -> int:
    return _int_env("LOGIN_THROTTLE_CLIENT_PER_WINDOW", 20, 1, 10000)


def email_fail_limit() -> int:
    return _int_env("LOGIN_THROTTLE_EMAIL_FAILS", 8, 1, 1000)


def global_limit() -> int:
    return _int_env("LOGIN_THROTTLE_GLOBAL_PER_HOUR", 400, 1, 1000000)


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


def email_key(email: str | None) -> str | None:
    if not email:
        return None
    norm = email.strip().lower()
    if not norm or "@" not in norm:
        return None
    return hashlib.sha256(f"{_SALT}:em:{norm}".encode()).hexdigest()[:20]


def _prune(dq: deque[float], cutoff: float) -> None:
    while dq and dq[0] < cutoff:
        dq.popleft()


def _retry_after(dq: deque[float], window: float, now: float) -> int:
    if not dq:
        return 1
    return max(1, int(dq[0] + window - now) + 1)


def check_attempt(
    *,
    key: str | None,
    email: str | None,
    now: float | None = None,
    record_client: bool = True,
) -> dict[str, Any]:
    """Decide whether this login/signup attempt may proceed to credential check.

    Returns {"allow": bool, "reason": str, "retry_after_sec": int}.
    When allowed and record_client is True, records the client+global hit.
    Email failure hits are recorded separately via record_failure().
    """
    t = time.time() if now is None else now
    with _lock:
        _stats["checked"] += 1
        if not enabled():
            _stats["allowed"] += 1
            return {"allow": True, "reason": "disabled", "retry_after_sec": 0}

        hour_cut = t - 3600.0
        win = float(window_sec())
        win_cut = t - win

        _prune(_global_hits, hour_cut)
        if len(_global_hits) >= global_limit():
            _stats["held_global"] += 1
            _stats["last_held_at"] = t
            return {
                "allow": False,
                "reason": "global_limit",
                "retry_after_sec": _retry_after(_global_hits, 3600.0, t),
            }

        ek = email_key(email)
        if ek:
            fails = _email_fails.setdefault(ek, deque())
            _prune(fails, win_cut)
            if len(fails) >= email_fail_limit():
                _stats["held_email"] += 1
                _stats["last_held_at"] = t
                return {
                    "allow": False,
                    "reason": "email_lockout",
                    "retry_after_sec": _retry_after(fails, win, t),
                }

        cli = None
        if key:
            cli = _client_hits.setdefault(key, deque())
            _prune(cli, win_cut)
            if len(cli) >= client_limit():
                _stats["held_client"] += 1
                _stats["last_held_at"] = t
                return {
                    "allow": False,
                    "reason": "client_limit",
                    "retry_after_sec": _retry_after(cli, win, t),
                }

        if record_client:
            _global_hits.append(t)
            if cli is not None:
                cli.append(t)
            if len(_client_hits) > 5000:
                for k in [k for k, v in _client_hits.items() if not v or v[-1] < win_cut][:2500]:
                    _client_hits.pop(k, None)
            if len(_email_fails) > 5000:
                for k in [k for k, v in _email_fails.items() if not v or v[-1] < win_cut][:2500]:
                    _email_fails.pop(k, None)

        _stats["allowed"] += 1
        return {"allow": True, "reason": "ok", "retry_after_sec": 0}


def record_failure(email: str | None, now: float | None = None) -> None:
    """Record a failed password/email check toward the email lockout window."""
    if not enabled():
        return
    ek = email_key(email)
    if not ek:
        return
    t = time.time() if now is None else now
    with _lock:
        fails = _email_fails.setdefault(ek, deque())
        fails.append(t)
        _stats["failures_recorded"] += 1


def clear_failures(email: str | None) -> None:
    """Successful login clears that email's failure window."""
    ek = email_key(email)
    if not ek:
        return
    with _lock:
        if ek in _email_fails:
            _email_fails.pop(ek, None)
            _stats["success_clears"] += 1


def summary() -> dict[str, Any]:
    now = time.time()
    with _lock:
        _prune(_global_hits, now - 3600.0)
        held = _stats["held_client"] + _stats["held_email"] + _stats["held_global"]
        return {
            "pack": PACK,
            "enabled": enabled(),
            "limits": {
                "client_per_window": client_limit(),
                "window_sec": window_sec(),
                "email_fails": email_fail_limit(),
                "global_per_hour": global_limit(),
            },
            "process": {
                "checked": _stats["checked"],
                "allowed": _stats["allowed"],
                "held_total": held,
                "held_client": _stats["held_client"],
                "held_email": _stats["held_email"],
                "held_global": _stats["held_global"],
                "failures_recorded": _stats["failures_recorded"],
                "success_clears": _stats["success_clears"],
                "attempts_last_hour": len(_global_hits),
                "tracked_clients": len(_client_hits),
                "tracked_emails": len(_email_fails),
                "last_held_at": _stats["last_held_at"],
                "since": _stats["since"],
            },
        }


def reset_for_test() -> None:
    with _lock:
        _client_hits.clear()
        _email_fails.clear()
        _global_hits.clear()
        for k in (
            "checked",
            "allowed",
            "held_client",
            "held_email",
            "held_global",
            "failures_recorded",
            "success_clears",
        ):
            _stats[k] = 0
        _stats["last_held_at"] = None

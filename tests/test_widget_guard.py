import os
import tempfile
import uuid
from pathlib import Path

os.environ["STARLING_DB"] = str(Path(tempfile.mkdtemp()) / "widget-guard.db")
os.environ.pop("SMTP_HOST", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)

import pytest
from fastapi.testclient import TestClient

from app import grok, widget_guard
from app.db import connect, init
from app.main import app

init()
c = TestClient(app)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    widget_guard.reset_for_test()
    for k in (
        "WIDGET_GUARD",
        "WIDGET_GUARD_CLIENT_AI_PER_WINDOW",
        "WIDGET_GUARD_LOCATION_AI_PER_HOUR",
        "WIDGET_GUARD_GLOBAL_AI_PER_HOUR",
        "WIDGET_GUARD_WINDOW_SEC",
        "WIDGET_GUARD_PROMPT_CHARS",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def location_slug():
    r = c.post(
        "/api/auth/signup",
        json={
            "name": "WG Owner",
            "email": f"wg-{uuid.uuid4().hex[:10]}@biz.test",
            "password": "password12",
            "business": "Guard Dental",
            "industry": "dental",
            "city": "Austin",
            "state": "TX",
        },
    )
    assert r.status_code == 200, r.text
    return c.get("/api/dashboard").json()["location"]["slug"]


def fake_grok(monkeypatch):
    calls = []

    async def _reply(business, channel, incoming):
        calls.append(incoming)
        return "AI says hi"

    monkeypatch.setattr(grok, "inbox_reply", _reply)
    return calls


def ai_usage_count(slug):
    con = connect()
    n = con.execute(
        "SELECT COUNT(*) FROM usage u JOIN locations l ON l.id = u.location_id WHERE l.slug = ? AND u.kind = 'ai'",
        (slug,),
    ).fetchone()[0]
    con.close()
    return n


def test_health_and_summary():
    assert c.get("/api/health").json()["widget_guard"] == "widget-guard-v1"
    s = c.get("/api/widget-guard/summary").json()
    assert s["ok"] is True and s["pack"] == "widget-guard-v1" and s["enabled"] is True
    assert s["limits"] == {
        "client_ai_per_window": 8,
        "window_sec": 600,
        "location_ai_per_hour": 60,
        "global_ai_per_hour": 300,
        "prompt_chars": 2000,
    }


def test_client_limit_falls_back_to_existing_reply(monkeypatch):
    monkeypatch.setenv("WIDGET_GUARD_CLIENT_AI_PER_WINDOW", "2")
    calls = fake_grok(monkeypatch)
    slug = location_slug()
    h = {"x-real-ip": "203.0.113.7"}
    replies = [c.post("/api/widget/chat", json={"slug": slug, "name": "Bot", "body": f"hi {i}"}, headers=h).json() for i in range(4)]
    assert [r["reply"] for r in replies[:2]] == ["AI says hi", "AI says hi"]
    assert replies[2]["reply"] == "Thanks — Guard Dental received this and will reply from the inbox."
    assert set(replies[2].keys()) == {"ok", "thread_id", "reply"}, "response shape unchanged"
    assert len(calls) == 2
    assert ai_usage_count(slug) == 2, "held messages are not metered as AI"
    # messages are still saved to the inbox
    threads = c.get("/api/inbox").json()["threads"]
    assert sum(1 for t in threads if t["name"] == "Bot") == 4
    # a different visitor still gets AI
    other = c.post("/api/widget/chat", json={"slug": slug, "body": "hello"}, headers={"x-real-ip": "198.51.100.9"}).json()
    assert other["reply"] == "AI says hi"
    s = c.get("/api/widget-guard/summary").json()["process"]
    assert s["held_client"] == 2 and s["ai_allowed"] == 3
    assert "203.0.113.7" not in str(c.get("/api/widget-guard/summary").json())


def test_location_and_global_limits(monkeypatch):
    fake_grok(monkeypatch)
    monkeypatch.setenv("WIDGET_GUARD_LOCATION_AI_PER_HOUR", "1")
    slug = location_slug()
    a = c.post("/api/widget/chat", json={"slug": slug, "body": "1"}, headers={"x-real-ip": "10.0.0.1"}).json()
    b = c.post("/api/widget/chat", json={"slug": slug, "body": "2"}, headers={"x-real-ip": "10.0.0.2"}).json()
    assert a["reply"] == "AI says hi" and b["reply"].startswith("Thanks — ")
    monkeypatch.setenv("WIDGET_GUARD_LOCATION_AI_PER_HOUR", "100")
    monkeypatch.setenv("WIDGET_GUARD_GLOBAL_AI_PER_HOUR", "2")
    d = c.post("/api/widget/chat", json={"slug": slug, "body": "3"}, headers={"x-real-ip": "10.0.0.3"}).json()
    e = c.post("/api/widget/chat", json={"slug": slug, "body": "4"}, headers={"x-real-ip": "10.0.0.4"}).json()
    assert d["reply"] == "AI says hi" and e["reply"].startswith("Thanks — ")
    p = c.get("/api/widget-guard/summary").json()["process"]
    assert p["held_location"] == 1 and p["held_global"] == 1


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("WIDGET_GUARD", "0")
    monkeypatch.setenv("WIDGET_GUARD_CLIENT_AI_PER_WINDOW", "1")
    calls = fake_grok(monkeypatch)
    slug = location_slug()
    for i in range(3):
        r = c.post("/api/widget/chat", json={"slug": slug, "body": "x" * 5000}, headers={"x-real-ip": "10.1.1.1"}).json()
        assert r["reply"] == "AI says hi"
    assert len(calls) == 3 and all(len(x) == 5000 for x in calls), "no trimming when disabled"
    assert c.get("/api/widget-guard/summary").json()["enabled"] is False


def test_prompt_trim_keeps_full_message_in_inbox(monkeypatch):
    calls = fake_grok(monkeypatch)
    slug = location_slug()
    long = "y" * 7000
    r = c.post("/api/widget/chat", json={"slug": slug, "name": "Long", "body": long}).json()
    assert r["reply"] == "AI says hi"
    assert len(calls[0]) == 2000
    con = connect()
    body = con.execute(
        "SELECT body FROM messages WHERE thread_id = ? AND direction = 'in'", (r["thread_id"],)
    ).fetchone()[0]
    con.close()
    assert body == long
    assert c.get("/api/widget-guard/summary").json()["process"]["prompts_trimmed"] == 1


def test_unknown_client_not_shared_bucket():
    widget_guard.reset_for_test()
    os.environ["WIDGET_GUARD_CLIENT_AI_PER_WINDOW"] = "1"
    try:
        for _ in range(3):
            assert widget_guard.check_ai(1, None)["allow"] is True
        k = widget_guard.client_key({"x-forwarded-for": "1.2.3.4, 5.6.7.8"}, None)
        assert k and len(k) == 20 and "1.2.3.4" not in k
        assert widget_guard.check_ai(2, k)["allow"] is True
        assert widget_guard.check_ai(2, k)["reason"] == "client_limit"
    finally:
        os.environ.pop("WIDGET_GUARD_CLIENT_AI_PER_WINDOW", None)


def test_window_expiry():
    os.environ["WIDGET_GUARD_CLIENT_AI_PER_WINDOW"] = "1"
    try:
        assert widget_guard.check_ai(3, "k", now=1000.0)["allow"] is True
        assert widget_guard.check_ai(3, "k", now=1100.0)["allow"] is False
        assert widget_guard.check_ai(3, "k", now=1000.0 + 601)["allow"] is True
    finally:
        os.environ.pop("WIDGET_GUARD_CLIENT_AI_PER_WINDOW", None)


def test_unknown_slug_still_404():
    assert c.post("/api/widget/chat", json={"slug": "nope-" + uuid.uuid4().hex[:6], "body": "x"}).status_code == 404

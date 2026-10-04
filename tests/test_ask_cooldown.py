import os
import tempfile
import time
import uuid
from pathlib import Path

os.environ["STARLING_DB"] = str(Path(tempfile.mkdtemp()) / "ask-cd.db")
os.environ.pop("SMTP_HOST", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)
os.environ["ASK_COOLDOWN"] = "1"
os.environ["ASK_COOLDOWN_HOURS"] = "72"

from fastapi.testclient import TestClient

from app import ask_cooldown
from app.db import connect, init
from app.main import app

init()
c = TestClient(app)


def signup():
    r = c.post(
        "/api/auth/signup",
        json={
            "name": "CD Owner",
            "email": f"cd-{uuid.uuid4().hex[:10]}@biz.test",
            "password": "password12",
            "business": "Cooldown Dental",
            "industry": "dental",
            "city": "Austin",
            "state": "TX",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_pack_id_and_summary():
    assert ask_cooldown.PACK == "ask-cooldown-v1"
    s = ask_cooldown.summary()
    assert s["enabled"] is True
    assert s["cooldown_hours"] == 72.0


def test_disabled_kill_switch(monkeypatch):
    monkeypatch.setenv("ASK_COOLDOWN", "0")
    assert ask_cooldown.enabled() is False
    assert ask_cooldown.guard_auto_delivery(1, 1, "sms") is None
    monkeypatch.setenv("ASK_COOLDOWN", "1")


def test_link_channel_never_held():
    assert ask_cooldown.guard_auto_delivery(1, 1, "link") is None


def test_health_marker():
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("ask_cooldown") == "ask-cooldown-v1"


def test_summary_endpoint():
    r = c.get("/api/ask-cooldown/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["pack"] == "ask-cooldown-v1"
    assert body["enabled"] is True


def test_holds_second_sms_email_after_sent():
    user = signup()
    lid = user["location_id"]
    cid = c.post(
        "/api/customers",
        json={"name": "Pat Lee", "email": "pat@example.com", "phone": "5125550199"},
    ).json()["id"]
    # Simulate a prior successful send without real SMTP/Twilio.
    con = connect()
    token = "prevtoken123"
    con.execute(
        """INSERT INTO review_requests (location_id, customer_id, token, channel, status, sent_at)
           VALUES (?,?,?,?,?,?)""",
        (lid, cid, token, "email", "sent", time.time() - 3600),
    )
    con.commit()
    con.close()

    sent = c.post("/api/review-requests", json={"customer_id": cid, "channel": "email"}).json()
    assert sent["ok"] is True
    assert sent["sent"] is False
    assert sent.get("held_for_ask_cooldown") is True
    assert (sent.get("delivery") or {}).get("ask_cooldown") is True
    assert (sent.get("delivery") or {}).get("skipped") is True
    assert sent.get("previous_token") == token
    assert "prevtoken123" in (sent.get("previous_link") or "")


def test_allows_when_outside_window(monkeypatch):
    monkeypatch.setenv("ASK_COOLDOWN_HOURS", "1")
    user = signup()
    lid = user["location_id"]
    cid = c.post(
        "/api/customers",
        json={"name": "Old Ask", "email": "old@example.com"},
    ).json()["id"]
    con = connect()
    con.execute(
        """INSERT INTO review_requests (location_id, customer_id, token, channel, status, sent_at)
           VALUES (?,?,?,?,?,?)""",
        (lid, cid, "oldtok", "email", "sent", time.time() - 7200),
    )
    con.commit()
    con.close()
    sent = c.post("/api/review-requests", json={"customer_id": cid, "channel": "email"}).json()
    # Outside 1h window → not held by cooldown (may still fail SMTP).
    assert sent.get("held_for_ask_cooldown") is not True
    assert (sent.get("delivery") or {}).get("ask_cooldown") is not True

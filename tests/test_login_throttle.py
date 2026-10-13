import os
import tempfile
import uuid
from pathlib import Path

os.environ["STARLING_DB"] = str(Path(tempfile.mkdtemp()) / "login-throttle.db")
os.environ.pop("SMTP_HOST", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)

import pytest
from fastapi.testclient import TestClient

from app import login_throttle
from app.db import init
from app.main import app

init()
c = TestClient(app)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    login_throttle.reset_for_test()
    for k in (
        "LOGIN_THROTTLE",
        "LOGIN_THROTTLE_CLIENT_PER_WINDOW",
        "LOGIN_THROTTLE_EMAIL_FAILS",
        "LOGIN_THROTTLE_WINDOW_SEC",
        "LOGIN_THROTTLE_GLOBAL_PER_HOUR",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def signup_user(email=None, password="password12"):
    email = email or f"lt-{uuid.uuid4().hex[:10]}@biz.test"
    r = c.post(
        "/api/auth/signup",
        json={
            "name": "LT Owner",
            "email": email,
            "password": password,
            "business": "Throttle Dental",
            "industry": "dental",
            "city": "Austin",
            "state": "TX",
        },
    )
    assert r.status_code == 200, r.text
    return email


def test_health_and_summary():
    assert c.get("/api/health").json()["login_throttle"] == "login-throttle-v1"
    s = c.get("/api/login-throttle/summary").json()
    assert s["ok"] is True and s["pack"] == "login-throttle-v1" and s["enabled"] is True
    assert s["limits"] == {
        "client_per_window": 20,
        "window_sec": 900,
        "email_fails": 8,
        "global_per_hour": 400,
    }


def test_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE", "0")
    assert login_throttle.enabled() is False
    for _ in range(5):
        assert login_throttle.check_attempt(key="k", email="a@b.c")["allow"] is True
        assert login_throttle.check_attempt(key="k", email="a@b.c")["reason"] == "disabled"


def test_client_limit_returns_429(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_CLIENT_PER_WINDOW", "3")
    email = signup_user()
    login_throttle.reset_for_test()  # clear signup hits
    h = {"x-real-ip": "198.51.100.9"}
    codes = []
    for i in range(5):
        r = c.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers=h,
        )
        codes.append(r.status_code)
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429 and codes[4] == 429
    body = c.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers=h,
    ).json()
    assert body["ok"] is False and body["reason"] == "client_limit"
    assert "Retry-After" in c.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers=h,
    ).headers


def test_email_lockout_after_failures(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_EMAIL_FAILS", "3")
    monkeypatch.setenv("LOGIN_THROTTLE_CLIENT_PER_WINDOW", "100")
    email = signup_user()
    login_throttle.reset_for_test()
    # different client IPs so only email bucket trips
    for i in range(3):
        r = c.post(
            "/api/auth/login",
            json={"email": email, "password": "nope"},
            headers={"x-real-ip": f"203.0.113.{i+1}"},
        )
        assert r.status_code == 401
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": "nope"},
        headers={"x-real-ip": "203.0.113.99"},
    )
    assert r.status_code == 429
    assert r.json()["reason"] == "email_lockout"


def test_success_clears_email_failures(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_EMAIL_FAILS", "5")
    monkeypatch.setenv("LOGIN_THROTTLE_CLIENT_PER_WINDOW", "100")
    email = signup_user(password="password12")
    login_throttle.reset_for_test()
    for i in range(3):
        assert (
            c.post(
                "/api/auth/login",
                json={"email": email, "password": "wrong"},
                headers={"x-real-ip": f"192.0.2.{i+1}"},
            ).status_code
            == 401
        )
    ok = c.post(
        "/api/auth/login",
        json={"email": email, "password": "password12"},
        headers={"x-real-ip": "192.0.2.50"},
    )
    assert ok.status_code == 200 and ok.json()["ok"] is True
    s = login_throttle.summary()
    assert s["process"]["success_clears"] >= 1
    # after clear, failures can accumulate again without immediate lockout
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong"},
        headers={"x-real-ip": "192.0.2.51"},
    )
    assert r.status_code == 401


def test_unit_window_expiry(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_CLIENT_PER_WINDOW", "1")
    monkeypatch.setenv("LOGIN_THROTTLE_WINDOW_SEC", "60")
    login_throttle.reset_for_test()
    assert login_throttle.check_attempt(key="k", email=None, now=1000.0)["allow"] is True
    assert login_throttle.check_attempt(key="k", email=None, now=1010.0)["allow"] is False
    assert login_throttle.check_attempt(key="k", email=None, now=1000.0 + 61)["allow"] is True


def test_unit_email_lock_and_clear(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_EMAIL_FAILS", "2")
    login_throttle.reset_for_test()
    assert login_throttle.check_attempt(key="a", email="u@x.test", now=1.0)["allow"] is True
    login_throttle.record_failure("u@x.test", now=1.0)
    login_throttle.record_failure("u@x.test", now=2.0)
    gate = login_throttle.check_attempt(key="b", email="u@x.test", now=3.0)
    assert gate["allow"] is False and gate["reason"] == "email_lockout"
    login_throttle.clear_failures("u@x.test")
    assert login_throttle.check_attempt(key="c", email="u@x.test", now=4.0)["allow"] is True


def test_global_limit(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_GLOBAL_PER_HOUR", "2")
    monkeypatch.setenv("LOGIN_THROTTLE_CLIENT_PER_WINDOW", "100")
    login_throttle.reset_for_test()
    assert login_throttle.check_attempt(key="1", email=None, now=5000.0)["allow"] is True
    assert login_throttle.check_attempt(key="2", email=None, now=5001.0)["allow"] is True
    gate = login_throttle.check_attempt(key="3", email=None, now=5002.0)
    assert gate["allow"] is False and gate["reason"] == "global_limit"

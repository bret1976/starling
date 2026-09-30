import os
from datetime import datetime
from zoneinfo import ZoneInfo

from app import quiet_hours


def test_pack_id():
    assert quiet_hours.PACK == "quiet-hours-v1"


def test_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    assert quiet_hours.is_quiet_now() is False
    assert quiet_hours.guard_auto_delivery("sms") is None
    assert quiet_hours.summary()["enabled"] is False


def test_window_midday(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS_START", "22:00")
    monkeypatch.setenv("QUIET_HOURS_END", "07:00")
    monkeypatch.setenv("QUIET_HOURS_TZ", "America/Los_Angeles")
    tz = ZoneInfo("America/Los_Angeles")
    noon = datetime(2026, 9, 29, 12, 0, tzinfo=tz)
    late = datetime(2026, 9, 29, 23, 30, tzinfo=tz)
    early = datetime(2026, 9, 30, 6, 0, tzinfo=tz)
    assert quiet_hours.is_quiet_now(noon) is False
    assert quiet_hours.is_quiet_now(late) is True
    assert quiet_hours.is_quiet_now(early) is True
    held = quiet_hours.guard_auto_delivery("sms")
    # may or may not be quiet depending on real now; force with monkeypatch of is_quiet_now
    monkeypatch.setattr(quiet_hours, "is_quiet_now", lambda now=None: True)
    held = quiet_hours.guard_auto_delivery("sms")
    assert held and held["skipped"] and held["quiet_hours"] is True
    assert quiet_hours.guard_auto_delivery("link") is None


def test_same_start_end_disables(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS_START", "09:00")
    monkeypatch.setenv("QUIET_HOURS_END", "09:00")
    assert quiet_hours.is_quiet_now() is False

from app import deliver


class R:
    def __init__(self, code, text="{}"):
        self.status_code = code
        self.text = text


def _env(monkeypatch, inkbox=True, smtp=False):
    for k in ("INKBOX_API_KEY", "INKBOX_FROM", "SMTP_HOST", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    if inkbox:
        monkeypatch.setenv("INKBOX_API_KEY", "test-key")
        monkeypatch.setenv("INKBOX_FROM", "box@inkboxmail.com")
    if smtp:
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "a@example.com")


def test_status_email_needs_key(monkeypatch):
    _env(monkeypatch, inkbox=False)
    assert deliver.status()["email"] is False
    _env(monkeypatch, inkbox=True)
    assert deliver.status()["email"] is True


def test_inkbox_send_request_shape(monkeypatch):
    _env(monkeypatch)
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return R(201)

    monkeypatch.setattr(deliver.httpx, "post", fake_post)
    out = deliver.send_email("x@example.com", "Hi", "Body")
    assert out["ok"] and out["provider"] == "inkbox"
    assert seen["url"] == "https://inkbox.ai/api/v1/mail/mailboxes/box@inkboxmail.com/messages"
    assert seen["headers"]["X-API-Key"] == "test-key"
    assert seen["json"] == {"recipients": {"to": ["x@example.com"]}, "subject": "Hi", "body_text": "Body"}


def test_inkbox_error_no_key_leak(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(deliver.httpx, "post", lambda *a, **k: R(401, "unauthorized"))
    out = deliver.send_email("x@example.com", "Hi", "Body")
    assert not out["ok"] and "401" in out["error"] and "test-key" not in out["error"]


def test_falls_back_to_smtp(monkeypatch):
    _env(monkeypatch, smtp=True)
    monkeypatch.setattr(deliver.httpx, "post", lambda *a, **k: R(500, "boom"))
    monkeypatch.setattr(deliver, "send_email_smtp", lambda to, s, b: {"ok": True, "channel": "email", "to": to})
    out = deliver.send_email("x@example.com", "Hi", "Body")
    assert out["ok"] and out["fallback_from"] == "inkbox"

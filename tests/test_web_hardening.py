import os
import tempfile
from pathlib import Path

os.environ.setdefault("STARLING_DB", str(Path(tempfile.mkdtemp()) / "h.db"))
os.environ["PUBLIC_URL"] = "https://starling.example"

from fastapi.testclient import TestClient

from app.db import init
from app.main import app
from app.web_hardening import PUBLIC_PAGES

init()
c = TestClient(app)
PAGES = PUBLIC_PAGES + ["/app", "/api/health", "/static/site.css", "/robots.txt", "/sitemap.xml"]


def test_head_matches_get():
    for p in PAGES:
        g = c.get(p)
        h = c.head(p)
        assert g.status_code == 200, p
        assert h.status_code == 200, p
        assert h.content == b"", p
        assert h.headers.get("content-type") == g.headers.get("content-type"), p


def test_security_headers_everywhere():
    for p in PAGES + ["/nope-404"]:
        r = c.get(p)
        for k in ["strict-transport-security", "content-security-policy", "x-frame-options",
                  "x-content-type-options", "referrer-policy", "permissions-policy"]:
            assert k in r.headers, (p, k)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_robots():
    r = c.get("/robots.txt")
    assert r.headers["content-type"].startswith("text/plain")
    t = r.text
    assert "Disallow: /app" in t and "Disallow: /api/" in t
    assert "Sitemap: https://starling.example/sitemap.xml" in t


def test_sitemap():
    r = c.get("/sitemap.xml")
    assert "xml" in r.headers["content-type"]
    for p in PUBLIC_PAGES:
        assert f"<loc>https://starling.example{p}</loc>" in r.text
    assert "/app<" not in r.text and "/api" not in r.text


def test_post_routes_unaffected():
    assert c.head("/api/auth/logout").status_code == 405

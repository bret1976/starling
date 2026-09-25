import os
import tempfile
import uuid
from pathlib import Path

os.environ["STARLING_DB"] = str(Path(tempfile.mkdtemp()) / "t.db")
os.environ.pop("SMTP_HOST", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)

from fastapi.testclient import TestClient

from app.db import init
from app.main import app

init()
c = TestClient(app)


def signup():
    r = c.post(
        "/api/auth/signup",
        json={
            "name": "Test Owner",
            "email": f"owner-{uuid.uuid4().hex[:10]}@biz.test",
            "password": "password12",
            "business": "Test Dental",
            "industry": "dental",
            "city": "Austin",
            "state": "TX",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_health():
    r = c.get("/api/health")
    assert r.json()["ok"] is True
    assert r.json()["product"] == "Starling"
    assert "delivery" in r.json()


def test_no_seed_users():
    r = c.post("/api/auth/login", json={"email": "demo@northside.dental", "password": "demo1234"})
    assert r.status_code == 401


def test_signup_empty_dashboard():
    signup()
    d = c.get("/api/dashboard").json()
    assert d["location"]["name"] == "Test Dental"
    assert d["stats"]["reviews"] == 0
    assert c.get("/api/customers").json()["customers"] == []
    assert c.get("/api/reviews").json()["reviews"] == []


def test_review_funnel_creates_live_link():
    signup()
    cid = c.post("/api/customers", json={"name": "Jordan Hale", "email": "jordan@example.com", "phone": "5125550100"}).json()["id"]
    sent = c.post("/api/review-requests", json={"customer_id": cid, "channel": "email"}).json()
    assert sent["link"].startswith("http")
    assert sent["token"]
    assert sent["sent"] is False
    assert "SMTP" in (sent["delivery"] or {}).get("error", "")
    token = sent["token"]
    pub = c.post(f"/api/public/review/{token}", json={"rating": 5, "text": "Loved it"})
    assert pub.status_code == 200
    assert pub.json()["public"] is True
    reviews = c.get("/api/reviews").json()["reviews"]
    assert any(r["text"] == "Loved it" for r in reviews)


def test_low_star_private_inbox():
    signup()
    cid = c.post("/api/customers", json={"name": "Sam", "email": "sam@example.com"}).json()["id"]
    token = c.post("/api/review-requests", json={"customer_id": cid, "channel": "email"}).json()["token"]
    c.post(f"/api/public/review/{token}", json={"rating": 2, "text": "Waited forever"})
    inbox = c.get("/api/inbox").json()["threads"]
    assert any(t["channel"] == "review" for t in inbox)


def test_widget_to_inbox():
    signup()
    slug = c.get("/api/dashboard").json()["location"]["slug"]
    r = c.post("/api/widget/chat", json={"slug": slug, "name": "Alex", "body": "Hours today?"})
    assert r.status_code == 200
    names = [t["name"] for t in c.get("/api/inbox").json()["threads"]]
    assert "Alex" in names


def test_form_to_inbox():
    signup()
    form = c.post("/api/forms", json={"name": "Contact", "fields": "name,message"}).json()
    assert form["url"].startswith("/f/")
    r = c.post(f"/api/public/form/{form['slug']}", json={"payload": {"name": "Pat", "message": "Need a cleaning"}})
    assert r.status_code == 200
    assert any(t["channel"] == "form" for t in c.get("/api/inbox").json()["threads"])


def test_listings_scan_real_osm():
    signup()
    c.patch("/api/location", json={"name": "Texas State Capitol", "address": "1100 Congress Ave", "city": "Austin", "state": "TX", "zip": "78701"})
    r = c.post("/api/listings/sync")
    assert r.status_code == 200, r.text
    dirs = [l["directory"] for l in r.json()["listings"]]
    assert "OpenStreetMap" in dirs
    assert "Google Business" in dirs


def test_ask_review_one_step():
    signup()
    d = c.post("/api/ask-review", json={"name": "Jordan Hale", "email": "jordan@example.com"}).json()
    assert d["link"].startswith("http")
    assert d["token"]
    assert d["sent"] is False
    home = c.get("/api/dashboard").json()
    assert home["stats"]["requests"] == 1
    assert home["pending"]
    token = d["token"]
    c.post(f"/api/public/review/{token}", json={"rating": 5, "text": "Loved it"})
    home = c.get("/api/dashboard").json()
    assert home["stats"]["reviews"] == 1
    assert any(r["text"] == "Loved it" for r in home["recent"])


def test_plan_gate_social():
    signup()
    r = c.get("/api/social")
    assert r.status_code == 402
    c.post("/api/location/plan", json={"plan": "growth"})
    assert c.get("/api/social").status_code == 200


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, type(e).__name__, e)
    raise SystemExit(failed)


def test_public_insights_themes():
    r = c.post(
        "/api/public/insights",
        json={"text": "5 - Dr. Patel was gentle and the front desk was friendly\n2 - Waited over an hour and nobody called back\n1 - Charged me twice, receptionist was rude"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["with_text"] == 3
    labels = {a["aspect"] for a in d["aspects"]}
    assert {"staff", "wait", "price"} <= labels
    assert d["fix_first"] in {"staff", "wait", "price", "communication"}
    assert c.post("/api/public/insights", json={"text": "  "}).status_code == 400


def test_dashboard_insights_requires_login_and_reads_reviews():
    fresh = TestClient(app)
    assert fresh.get("/api/insights").status_code == 401
    user = signup()
    from app.db import connect as _connect
    con = _connect()
    con.execute(
        "INSERT INTO reviews (location_id, source, rating, text, public, created_at) VALUES (?,?,?,?,?,?)",
        (user["location_id"], "starling", 5, "Great work but they charged me twice.", 1, 1.0),
    )
    con.commit()
    con.close()
    r = c.get("/api/insights")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["reviews"] == 1
    assert d["mismatches"] and d["mismatches"][0]["rating"] == 5
    assert c.get("/review-analyzer").status_code == 200

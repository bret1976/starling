from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import deliver, grok, insights, places
from app.db import PLANS, SMS_COST, check_pw, connect, create_location, hash_pw, init, row, rows, slugify

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="Starling")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.on_event("startup")
def _startup():
    init()


def user_from(request: Request):
    token = request.cookies.get("sid")
    if not token:
        return None
    con = connect()
    u = con.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ?""",
        (token,),
    ).fetchone()
    con.close()
    return row(u)


def require(request: Request):
    u = user_from(request)
    if not u:
        raise HTTPException(401, "sign in")
    return u


def loc_id(user: dict, request: Request) -> int:
    if user["role"] == "agency":
        q = request.query_params.get("location_id")
        if q:
            return int(q)
        con = connect()
        first = con.execute("SELECT id FROM locations ORDER BY id LIMIT 1").fetchone()
        con.close()
        if not first:
            raise HTTPException(404, "no locations")
        return first["id"]
    if not user.get("location_id"):
        raise HTTPException(403, "no location")
    return int(user["location_id"])


def location(lid: int) -> dict:
    con = connect()
    loc = row(con.execute("SELECT * FROM locations WHERE id = ?", (lid,)).fetchone())
    con.close()
    if not loc:
        raise HTTPException(404, "location missing")
    return loc


def gated(loc: dict, feature: str):
    plan = PLANS.get(loc["plan"]) or PLANS["starter"]
    if feature not in plan["features"]:
        raise HTTPException(402, f"{feature} requires {plan['name']} upgrade")


def settings() -> dict:
    con = connect()
    s = row(con.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    con.close()
    return s or {"rebill_sms": 2.0, "rebill_ai": 1.5, "brand": "Starling"}


def meter(lid: int, kind: str, units: int = 1):
    s = settings()
    cost = SMS_COST * units if kind == "sms" else 0.02 * units
    mult = s["rebill_sms"] if kind == "sms" else s["rebill_ai"]
    charged = round(cost * mult, 4)
    con = connect()
    con.execute(
        "INSERT INTO usage (location_id, kind, units, cost, charged, created_at) VALUES (?,?,?,?,?,?)",
        (lid, kind, units, round(cost, 4), charged, time.time()),
    )
    con.commit()
    con.close()
    return {"cost": round(cost, 4), "charged": charged, "profit": round(charged - cost, 4)}


class LoginIn(BaseModel):
    email: str
    password: str


class SignupIn(BaseModel):
    name: str
    email: str
    password: str
    business: str
    industry: str = "local"
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    place_id: str = ""
    role: str = "staff"


class LocationIn(BaseModel):
    name: str | None = None
    industry: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    website: str | None = None
    place_id: str | None = None
    google_url: str | None = None


class FormIn(BaseModel):
    name: str
    fields: str = "name,email,phone,message"


class FormSubmitIn(BaseModel):
    payload: dict


class ReferralIn(BaseModel):
    name: str
    email: str = ""


class CustomerIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""


class VisitIn(BaseModel):
    customer_id: int
    note: str = ""


class RequestReviewIn(BaseModel):
    customer_id: int
    channel: str = "sms"


class AskReviewIn(BaseModel):
    name: str
    email: str = ""
    phone: str = ""


class ReviewReplyIn(BaseModel):
    text: str


class MessageIn(BaseModel):
    body: str


class WidgetIn(BaseModel):
    slug: str
    name: str = "Website visitor"
    body: str


class SocialIn(BaseModel):
    body: str
    platform: str = "instagram"
    status: str = "draft"


class WorkflowIn(BaseModel):
    name: str
    trigger: str
    action: str


class PlanIn(BaseModel):
    plan: str


class RebillIn(BaseModel):
    rebill_sms: float = Field(ge=1, le=10)
    rebill_ai: float = Field(ge=1, le=10)


class PublicReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = ""


class ListingDescIn(BaseModel):
    pass


@app.get("/")
def landing():
    return FileResponse(STATIC / "index.html")


@app.get("/app")
def dashboard():
    return FileResponse(STATIC / "app.html")


def _page(name: str):
    path = STATIC / f"{name}.html"
    if not path.exists():
        raise HTTPException(404, "missing")
    return FileResponse(path)


@app.get("/products")
def products_page():
    return _page("products")


@app.get("/solutions")
def solutions_page():
    return _page("solutions")


@app.get("/industries")
def industries_page():
    return _page("industries")


@app.get("/customers")
def customers_page():
    return _page("customers")


@app.get("/resources")
def resources_page():
    return _page("resources")


@app.get("/pricing")
def pricing_page():
    return _page("pricing")


@app.get("/demo")
def demo_page():
    return _page("demo")


@app.get("/r/{token}")
def review_page(token: str):
    return FileResponse(STATIC / "review.html")


@app.get("/p/{slug}")
def location_page(slug: str):
    return FileResponse(STATIC / "page.html")


@app.get("/f/{slug}")
def form_page(slug: str):
    return FileResponse(STATIC / "form.html")


@app.get("/review-analyzer")
def review_analyzer_page():
    return _page("analyzer")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "product": "Starling",
        "has_key": grok.has_key(),
        "model": grok.MODEL,
        "delivery": deliver.status(),
        "public_url": deliver.public_url(),
    }


@app.post("/api/auth/login")
def login(request: Request, body: LoginIn):
    con = connect()
    u = row(con.execute("SELECT * FROM users WHERE email = ?", (body.email.strip().lower(),)).fetchone())
    if not u or not check_pw(body.password, u["password_hash"]):
        con.close()
        raise HTTPException(401, "invalid email or password")
    token = secrets.token_urlsafe(24)
    con.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)", (token, u["id"], time.time()))
    con.commit()
    con.close()
    resp = JSONResponse({"ok": True, "user": public_user(u)})
    secure = (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
    resp.set_cookie("sid", token, httponly=True, samesite="lax", secure=secure, max_age=60 * 60 * 24 * 14)
    return resp


@app.post("/api/auth/signup")
def signup(request: Request, body: SignupIn):
    email = body.email.strip().lower()
    if len(body.password) < 8:
        raise HTTPException(400, "password must be 8+ characters")
    if not body.business.strip():
        raise HTTPException(400, "business name required")
    con = connect()
    if con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        con.close()
        raise HTTPException(409, "email already registered")
    role = "agency" if body.role == "agency" else "staff"
    lid = create_location(
        con,
        name=body.business.strip(),
        industry=body.industry.strip() or "local",
        phone=body.phone.strip(),
        email=email,
        address=body.address.strip(),
        city=body.city.strip(),
        state=body.state.strip(),
        zip=body.zip.strip(),
        place_id=body.place_id.strip(),
        plan="starter",
    )
    uid = con.execute(
        "INSERT INTO users (email, name, password_hash, role, location_id) VALUES (?,?,?,?,?)",
        (email, body.name.strip(), hash_pw(body.password), role, lid),
    ).lastrowid
    token = secrets.token_urlsafe(24)
    con.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)", (token, uid, time.time()))
    u = row(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
    con.commit()
    con.close()
    resp = JSONResponse({"ok": True, "user": public_user(u)})
    secure = (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
    resp.set_cookie("sid", token, httponly=True, samesite="lax", secure=secure, max_age=60 * 60 * 24 * 14)
    return resp


class DemoLeadIn(BaseModel):
    name: str
    email: str
    business: str = ""
    phone: str = ""


@app.post("/api/demo")
def demo_lead(body: DemoLeadIn):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "valid email required")
    con = connect()
    con.execute(
        "INSERT INTO demos (name, email, business, phone, created_at) VALUES (?,?,?,?,?)",
        (body.name.strip(), email, body.business.strip(), body.phone.strip(), time.time()),
    )
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(request: Request):
    token = request.cookies.get("sid")
    if token:
        con = connect()
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))
        con.commit()
        con.close()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sid")
    return resp


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "location_id": u["location_id"]}


@app.get("/api/me")
def me(request: Request):
    u = require(request)
    con = connect()
    locations = rows(con.execute("SELECT id, name, slug, plan, industry FROM locations ORDER BY name"))
    con.close()
    return {"user": public_user(u), "locations": locations, "plans": PLANS, "settings": settings()}


@app.get("/api/dashboard")
def dashboard_api(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    con = connect()
    stats = {
        "reviews": con.execute("SELECT COUNT(*) c FROM reviews WHERE location_id = ? AND public = 1", (lid,)).fetchone()["c"],
        "avg": round(
            con.execute(
                "SELECT COALESCE(AVG(rating),0) a FROM reviews WHERE location_id = ? AND public = 1",
                (lid,),
            ).fetchone()["a"],
            2,
        ),
        "unreplied": con.execute(
            "SELECT COUNT(*) c FROM reviews WHERE location_id = ? AND reply IS NULL",
            (lid,),
        ).fetchone()["c"],
        "open_threads": con.execute(
            "SELECT COUNT(*) c FROM threads WHERE location_id = ? AND status = 'open'",
            (lid,),
        ).fetchone()["c"],
        "listing_issues": con.execute(
            "SELECT COUNT(*) c FROM listings WHERE location_id = ? AND (status != 'synced' OR nap_match = 0)",
            (lid,),
        ).fetchone()["c"],
        "requests": con.execute(
            "SELECT COUNT(*) c FROM review_requests WHERE location_id = ?",
            (lid,),
        ).fetchone()["c"],
    }
    recent = rows(
        con.execute(
            """SELECT r.id, r.source, r.rating, r.text, r.public, r.reply, r.reply_draft, r.created_at,
                      c.name AS customer_name
               FROM reviews r LEFT JOIN customers c ON c.id = r.customer_id
               WHERE r.location_id = ? ORDER BY r.created_at DESC LIMIT 8""",
            (lid,),
        )
    )
    pending = rows(
        con.execute(
            """SELECT rr.id, rr.token, rr.channel, rr.status, rr.sent_at, c.name AS customer_name
               FROM review_requests rr JOIN customers c ON c.id = rr.customer_id
               WHERE rr.location_id = ? AND rr.status != 'completed'
               ORDER BY rr.sent_at DESC LIMIT 8""",
            (lid,),
        )
    )
    private = rows(
        con.execute(
            """SELECT id, name, channel, status, updated_at FROM threads
               WHERE location_id = ? AND channel = 'review' AND status = 'open'
               ORDER BY updated_at DESC LIMIT 6""",
            (lid,),
        )
    )
    usage = rows(
        con.execute(
            "SELECT kind, SUM(units) units, SUM(cost) cost, SUM(charged) charged FROM usage WHERE location_id = ? GROUP BY kind",
            (lid,),
        )
    )
    con.close()
    for p in pending:
        p["link"] = absolute_link(f"/r/{p['token']}", request)
    return {
        "location": loc,
        "stats": stats,
        "recent": recent,
        "pending": pending,
        "private": private,
        "usage": usage,
        "plan": PLANS[loc["plan"]],
        "delivery": deliver.status(),
    }


@app.get("/api/customers")
def list_customers(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    data = rows(con.execute("SELECT * FROM customers WHERE location_id = ? ORDER BY name", (lid,)))
    con.close()
    return {"customers": data}


@app.post("/api/customers")
def add_customer(request: Request, body: CustomerIn):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    cid = con.execute(
        "INSERT INTO customers (location_id, name, phone, email) VALUES (?,?,?,?)",
        (lid, body.name.strip(), body.phone.strip(), body.email.strip()),
    ).lastrowid
    con.commit()
    con.close()
    return {"ok": True, "id": cid}


@app.post("/api/visits")
def add_visit(request: Request, body: VisitIn):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    vid = con.execute(
        "INSERT INTO visits (location_id, customer_id, at, note) VALUES (?,?,?,?)",
        (lid, body.customer_id, time.strftime("%Y-%m-%d %H:%M"), body.note),
    ).lastrowid
    wf = rows(
        con.execute(
            "SELECT * FROM workflows WHERE location_id = ? AND enabled = 1 AND trigger = 'visit.created'",
            (lid,),
        )
    )
    con.commit()
    con.close()
    fired = []
    for w in wf:
        if w["action"] in {"sms.review_request", "review.request"}:
            channel = "email" if not deliver.sms_ready() else "sms"
            fired.append(send_review_request(lid, body.customer_id, channel, request))
    return {"ok": True, "id": vid, "automations": fired}


@app.get("/api/reviews")
def list_reviews(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    data = rows(
        con.execute(
            """SELECT r.*, c.name AS customer_name FROM reviews r
               LEFT JOIN customers c ON c.id = r.customer_id
               WHERE r.location_id = ? ORDER BY r.created_at DESC""",
            (lid,),
        )
    )
    con.close()
    return {"reviews": data}


@app.get("/api/insights")
def review_insights(request: Request):
    """Aspect-based sentiment over every review for the location (public + private)."""
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    data = rows(
        con.execute(
            "SELECT id, rating, text, public, created_at FROM reviews WHERE location_id = ? ORDER BY created_at DESC LIMIT 2000",
            (lid,),
        )
    )
    con.close()
    return insights.summarize(data)


class PublicInsightsIn(BaseModel):
    reviews: list[dict] = Field(default_factory=list)
    text: str = ""


@app.post("/api/public/insights")
def public_insights(body: PublicInsightsIn):
    """Free review analyzer (marketing tool): paste reviews, get themes. No login, no AI cost."""
    items: list[dict] = []
    for r in body.reviews[:200]:
        text = str((r or {}).get("text") or "")[:2000]
        rating = (r or {}).get("rating")
        try:
            rating = float(rating) if rating not in (None, "") else None
        except (TypeError, ValueError):
            rating = None
        if text.strip():
            items.append({"text": text, "rating": rating})
    if not items and body.text.strip():
        for line in body.text[:60000].splitlines():
            line = line.strip()
            if not line:
                continue
            rating = None
            m = re.match(r"^\s*([1-5])\s*(?:\*|★|stars?)?\s*[-:|,]\s*(.+)$", line)
            if m:
                rating, line = float(m.group(1)), m.group(2)
            items.append({"text": line[:2000], "rating": rating})
            if len(items) >= 200:
                break
    if not items:
        raise HTTPException(400, "paste at least one review")
    return insights.summarize(items)


@app.post("/api/review-requests")
async def create_request(request: Request, body: RequestReviewIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "reviews")
    return send_review_request(lid, body.customer_id, body.channel, request)


@app.post("/api/ask-review")
def ask_review(request: Request, body: AskReviewIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "reviews")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    email = body.email.strip()
    phone = body.phone.strip()
    con = connect()
    cid = con.execute(
        "INSERT INTO customers (location_id, name, phone, email) VALUES (?,?,?,?)",
        (lid, name, phone, email),
    ).lastrowid
    con.commit()
    con.close()
    if phone and deliver.sms_ready():
        channel = "sms"
    elif email:
        channel = "email"
    else:
        channel = "link"
    return send_review_request(lid, cid, channel, request)


def log_delivery(lid: int, channel: str, recipient: str, body: str, result: dict):
    con = connect()
    con.execute(
        """INSERT INTO deliveries (location_id, channel, recipient, body, ok, error, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (lid, channel, recipient, body, 1 if result.get("ok") else 0, result.get("error"), time.time()),
    )
    con.commit()
    con.close()


def absolute_link(path: str, request: Request | None = None) -> str:
    base = os.environ.get("PUBLIC_URL")
    if base:
        return base.rstrip("/") + path
    if request:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            return f"{proto}://{host}{path}"
    return deliver.public_url() + path


def send_review_request(lid: int, customer_id: int, channel: str, request: Request | None = None) -> dict:
    loc = location(lid)
    con = connect()
    cust = row(con.execute("SELECT * FROM customers WHERE id = ? AND location_id = ?", (customer_id, lid)).fetchone())
    if not cust:
        con.close()
        raise HTTPException(404, "customer missing")
    if channel == "sms" and not cust.get("phone"):
        channel = "email" if cust.get("email") else "link"
    if channel == "email" and not cust.get("email"):
        channel = "link"
    token = secrets.token_urlsafe(10)
    con.execute(
        """INSERT INTO review_requests (location_id, customer_id, token, channel, status, sent_at)
           VALUES (?,?,?,?,?,?)""",
        (lid, customer_id, token, channel, "queued", time.time()),
    )
    link = absolute_link(f"/r/{token}", request)
    first = (cust["name"] or "there").split()[0]
    body = f"Hi {first}, it's {loc['name']}. If we earned it, would you share a 30-second review? {link}"
    con.commit()
    con.close()
    if channel == "link":
        result = {
            "ok": False,
            "skipped": True,
            "error": "Copy the link and send it. Connect email or SMS later to send automatically.",
        }
    else:
        result = deliver.deliver(channel, cust, f"Review request from {loc['name']}", body)
        log_delivery(lid, channel, cust.get("phone") if channel == "sms" else cust.get("email"), body, result)
    if result.get("ok"):
        status = "sent"
    elif channel == "link" or result.get("skipped"):
        status = "ready"
    else:
        status = "failed"
    con = connect()
    con.execute(
        "UPDATE review_requests SET status = ? WHERE token = ?",
        (status, token),
    )
    con.commit()
    con.close()
    bill = meter(lid, "sms" if channel == "sms" else "email", 1) if result.get("ok") else None
    return {
        "ok": True,
        "token": token,
        "link": link,
        "message": body,
        "usage": bill,
        "delivery": result,
        "sent": bool(result.get("ok")),
    }


@app.get("/api/public/review/{token}")
def public_review_get(token: str):
    con = connect()
    req = row(
        con.execute(
            """SELECT rr.*, c.name AS customer_name, l.name AS business, l.slug
               FROM review_requests rr
               JOIN customers c ON c.id = rr.customer_id
               JOIN locations l ON l.id = rr.location_id
               WHERE rr.token = ?""",
            (token,),
        ).fetchone()
    )
    con.close()
    if not req:
        raise HTTPException(404, "link expired")
    return req


@app.post("/api/public/review/{token}")
def public_review_post(token: str, body: PublicReviewIn):
    con = connect()
    req = row(con.execute("SELECT * FROM review_requests WHERE token = ?", (token,)).fetchone())
    if not req:
        con.close()
        raise HTTPException(404, "link expired")
    public = 1 if body.rating >= 4 else 0
    rid = con.execute(
        """INSERT INTO reviews (location_id, customer_id, source, rating, text, public, reply, created_at)
           VALUES (?,?,?,?,?,?,NULL,?)""",
        (req["location_id"], req["customer_id"], "request", body.rating, body.text.strip(), public, time.time()),
    ).lastrowid
    con.execute("UPDATE review_requests SET status = 'completed' WHERE id = ?", (req["id"],))
    loc = row(con.execute("SELECT * FROM locations WHERE id = ?", (req["location_id"],)).fetchone())
    if body.rating <= 3:
        tid = con.execute(
            """INSERT INTO threads (location_id, customer_id, channel, name, status, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (req["location_id"], req["customer_id"], "review", "Private feedback", "open", time.time()),
        ).lastrowid
        con.execute(
            "INSERT INTO messages (thread_id, direction, body, created_at) VALUES (?,?,?,?)",
            (tid, "in", f"{body.rating}★ private feedback: {body.text or '(no comment)'}", time.time()),
        )
    con.commit()
    con.close()
    return {
        "ok": True,
        "id": rid,
        "public": bool(public),
        "google_url": places.google_review_url(loc.get("place_id"), loc.get("google_url")) if public else None,
        "message": "Please share on Google — that's the review that gets you found."
        if public
        else "Thank you. We captured this privately so we can make it right.",
    }


@app.post("/api/reviews/{rid}/reply")
def reply_review(rid: int, request: Request, body: ReviewReplyIn):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    r = row(con.execute("SELECT * FROM reviews WHERE id = ? AND location_id = ?", (rid, lid)).fetchone())
    if not r:
        con.close()
        raise HTTPException(404, "review missing")
    con.execute("UPDATE reviews SET reply = ? WHERE id = ?", (body.text.strip(), rid))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/reviews/{rid}/ai-reply")
async def ai_reply_review(rid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    gated(loc, "reviews")
    con = connect()
    r = row(con.execute("SELECT * FROM reviews WHERE id = ? AND location_id = ?", (rid, lid)).fetchone())
    con.close()
    if not r:
        raise HTTPException(404, "review missing")
    text = await grok.review_reply(loc["name"], loc["industry"], r["rating"], r["text"] or "")
    meter(lid, "ai", 1)
    con = connect()
    con.execute("UPDATE reviews SET reply_draft = ? WHERE id = ?", (text, rid))
    con.commit()
    con.close()
    return {"ok": True, "text": text}


@app.post("/api/reviews/{rid}/send-ai")
async def send_ai_review(rid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    gated(loc, "reviews")
    con = connect()
    r = row(con.execute("SELECT * FROM reviews WHERE id = ? AND location_id = ?", (rid, lid)).fetchone())
    if not r:
        con.close()
        raise HTTPException(404, "review missing")
    text = (r.get("reply_draft") or "").strip()
    con.close()
    if not text:
        text = await grok.review_reply(loc["name"], loc["industry"], r["rating"], r["text"] or "")
        meter(lid, "ai", 1)
    con = connect()
    con.execute("UPDATE reviews SET reply = ?, reply_draft = ? WHERE id = ?", (text, text, rid))
    con.commit()
    con.close()
    return {"ok": True, "text": text}


@app.get("/api/inbox")
def inbox(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "inbox")
    con = connect()
    threads = rows(
        con.execute(
            """SELECT t.*, (SELECT body FROM messages m WHERE m.thread_id = t.id ORDER BY id DESC LIMIT 1) AS preview
               FROM threads t WHERE t.location_id = ? ORDER BY t.updated_at DESC""",
            (lid,),
        )
    )
    con.close()
    return {"threads": threads}


@app.get("/api/inbox/{tid}")
def thread(tid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    t = row(con.execute("SELECT * FROM threads WHERE id = ? AND location_id = ?", (tid, lid)).fetchone())
    if not t:
        con.close()
        raise HTTPException(404, "thread missing")
    msgs = rows(con.execute("SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (tid,)))
    con.close()
    return {"thread": t, "messages": msgs}


@app.post("/api/inbox/{tid}/reply")
def inbox_reply(tid: int, request: Request, body: MessageIn):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    t = row(con.execute("SELECT * FROM threads WHERE id = ? AND location_id = ?", (tid, lid)).fetchone())
    if not t:
        con.close()
        raise HTTPException(404, "thread missing")
    cust = (
        row(con.execute("SELECT * FROM customers WHERE id = ?", (t["customer_id"],)).fetchone())
        if t.get("customer_id")
        else None
    )
    con.execute(
        "INSERT INTO messages (thread_id, direction, body, created_at) VALUES (?,?,?,?)",
        (tid, "out", body.body.strip(), time.time()),
    )
    con.execute("UPDATE threads SET updated_at = ?, status = 'open' WHERE id = ?", (time.time(), tid))
    con.commit()
    con.close()
    bill = None
    delivery = None
    if cust:
        loc = location(lid)
        delivery = deliver.deliver(t["channel"], cust, f"Message from {loc['name']}", body.body.strip())
        log_delivery(lid, t["channel"], cust.get("email") or cust.get("phone"), body.body.strip(), delivery)
        if delivery.get("ok") and t["channel"] == "sms":
            bill = meter(lid, "sms", 1)
    return {"ok": True, "usage": bill, "delivery": delivery, "sent": bool(delivery and delivery.get("ok"))}


@app.post("/api/inbox/{tid}/ai-reply")
async def inbox_ai(tid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    con = connect()
    t = row(con.execute("SELECT * FROM threads WHERE id = ? AND location_id = ?", (tid, lid)).fetchone())
    last = row(
        con.execute(
            "SELECT body FROM messages WHERE thread_id = ? AND direction = 'in' ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
    )
    con.close()
    if not t:
        raise HTTPException(404, "thread missing")
    text = await grok.inbox_reply(loc["name"], t["channel"], (last or {}).get("body") or "")
    meter(lid, "ai", 1)
    return {"ok": True, "text": text}


@app.post("/api/widget/chat")
async def widget_chat(body: WidgetIn):
    con = connect()
    loc = row(con.execute("SELECT * FROM locations WHERE slug = ?", (body.slug,)).fetchone())
    if not loc:
        con.close()
        raise HTTPException(404, "location missing")
    tid = con.execute(
        """INSERT INTO threads (location_id, customer_id, channel, name, status, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (loc["id"], None, "webchat", body.name.strip() or "Website visitor", "open", time.time()),
    ).lastrowid
    con.execute(
        "INSERT INTO messages (thread_id, direction, body, created_at) VALUES (?,?,?,?)",
        (tid, "in", body.body.strip(), time.time()),
    )
    con.commit()
    con.close()
    try:
        reply = await grok.inbox_reply(loc["name"], "webchat", body.body.strip())
        meter(loc["id"], "ai", 1)
    except Exception:
        reply = f"Thanks — {loc['name']} received this and will reply from the inbox."
    con = connect()
    con.execute(
        "INSERT INTO messages (thread_id, direction, body, created_at) VALUES (?,?,?,?)",
        (tid, "out", reply, time.time()),
    )
    con.commit()
    con.close()
    return {"ok": True, "thread_id": tid, "reply": reply}


@app.get("/api/listings")
def listings(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "listings")
    con = connect()
    data = rows(con.execute("SELECT * FROM listings WHERE location_id = ? ORDER BY directory", (lid,)))
    con.close()
    loc = location(lid)
    return {"listings": data, "nap": {k: loc[k] for k in ("name", "phone", "address", "city", "state", "zip")}}


@app.post("/api/listings/sync")
def listings_sync(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    scan = places.scan_nap(loc["name"], loc.get("address") or "", loc.get("city") or "", loc.get("state") or "", loc.get("zip") or "")
    if not scan.get("ok"):
        raise HTTPException(502, scan.get("error") or "listing scan failed")
    now = time.time()
    con = connect()
    if scan.get("lat"):
        con.execute("UPDATE locations SET lat = ?, lng = ? WHERE id = ?", (scan["lat"], scan["lng"], lid))
    existing = {r["directory"]: r for r in rows(con.execute("SELECT * FROM listings WHERE location_id = ?", (lid,)))}

    def upsert(directory: str, status: str, nap: int, url: str | None, detail: str | None):
        if directory in existing:
            con.execute(
                "UPDATE listings SET status = ?, nap_match = ?, last_sync = ?, url = ?, detail = ? WHERE id = ?",
                (status, nap, now, url, detail, existing[directory]["id"]),
            )
        else:
            con.execute(
                "INSERT INTO listings (location_id, directory, status, nap_match, last_sync, url, detail) VALUES (?,?,?,?,?,?,?)",
                (lid, directory, status, nap, now, url, detail),
            )

    osm_status = "found" if scan.get("found") else "not_found"
    upsert("OpenStreetMap", osm_status, 1 if scan.get("found") else 0, None, scan.get("label"))
    if loc.get("place_id"):
        upsert(
            "Google Business",
            "connected",
            1,
            places.google_review_url(loc["place_id"], loc.get("google_url")),
            loc["place_id"],
        )
    else:
        upsert("Google Business", "needs_place_id", 0, loc.get("google_url"), "Add a Google Place ID in Settings")
    con.commit()
    data = rows(con.execute("SELECT * FROM listings WHERE location_id = ? ORDER BY directory", (lid,)))
    con.close()
    return {"ok": True, "listings": data, "scan": scan}


@app.post("/api/listings/ai-description")
async def listings_ai(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    gated(loc, "listings")
    text = await grok.listing_description(loc["name"], loc["industry"], loc["city"])
    meter(lid, "ai", 1)
    return {"ok": True, "text": text}


@app.get("/api/social")
def social(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "social")
    con = connect()
    data = rows(
        con.execute("SELECT * FROM social_posts WHERE location_id = ? ORDER BY created_at DESC", (lid,))
    )
    con.close()
    return {"posts": data}


@app.post("/api/social")
def social_create(request: Request, body: SocialIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "social")
    con = connect()
    pid = con.execute(
        """INSERT INTO social_posts (location_id, body, platform, status, scheduled_at, created_at)
           VALUES (?,?,?,?,?,?)""",
        (lid, body.body.strip(), body.platform, body.status, time.time() + 86400 if body.status == "scheduled" else None, time.time()),
    ).lastrowid
    con.commit()
    con.close()
    return {"ok": True, "id": pid}


@app.post("/api/social/ai")
async def social_ai(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    gated(loc, "social")
    text = await grok.social_post(loc["name"], loc["industry"], "this week")
    meter(lid, "ai", 1)
    return {"ok": True, "text": text}


@app.post("/api/social/{pid}/publish")
def social_publish(pid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    con = connect()
    post = row(con.execute("SELECT * FROM social_posts WHERE id = ? AND location_id = ?", (pid, lid)).fetchone())
    if not post:
        con.close()
        raise HTTPException(404, "post missing")
    con.execute(
        "UPDATE social_posts SET status = 'published', scheduled_at = ? WHERE id = ?",
        (time.time(), pid),
    )
    con.commit()
    con.close()
    page = absolute_link(f"/p/{loc['slug']}", request)
    return {"ok": True, "url": page, "note": "Published on your Starling location page."}


@app.get("/api/workflows")
def workflows(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "workflows")
    con = connect()
    data = rows(con.execute("SELECT * FROM workflows WHERE location_id = ?", (lid,)))
    con.close()
    return {"workflows": data}


@app.post("/api/workflows")
def workflow_create(request: Request, body: WorkflowIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "workflows")
    con = connect()
    wid = con.execute(
        "INSERT INTO workflows (location_id, name, trigger, action, enabled) VALUES (?,?,?,?,1)",
        (lid, body.name, body.trigger, body.action),
    ).lastrowid
    con.commit()
    con.close()
    return {"ok": True, "id": wid}


@app.post("/api/workflows/{wid}/run")
async def workflow_run(wid: int, request: Request):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    w = row(con.execute("SELECT * FROM workflows WHERE id = ? AND location_id = ?", (wid, lid)).fetchone())
    if not w:
        con.close()
        raise HTTPException(404, "workflow missing")
    cust = row(con.execute("SELECT id FROM customers WHERE location_id = ? ORDER BY id LIMIT 1", (lid,)).fetchone())
    con.execute("UPDATE workflows SET last_run = ? WHERE id = ?", (time.time(), wid))
    con.commit()
    con.close()
    result = None
    if w["action"] in {"sms.review_request", "review.request"} and cust:
        channel = "email" if not deliver.sms_ready() else "sms"
        result = send_review_request(lid, cust["id"], channel, request)
    elif w["action"] == "social.draft":
        loc = location(lid)
        try:
            text = await grok.social_post(loc["name"], loc["industry"], "this week")
        except Exception as e:
            text = None
            result = {"ok": False, "error": str(e)}
        if text:
            con = connect()
            pid = con.execute(
                """INSERT INTO social_posts (location_id, body, platform, status, scheduled_at, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (lid, text, "starling", "draft", None, time.time()),
            ).lastrowid
            con.commit()
            con.close()
            result = {"ok": True, "post_id": pid, "text": text}
    return {"ok": True, "result": result}


@app.get("/api/referrals")
def referrals(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "referrals")
    con = connect()
    data = rows(con.execute("SELECT * FROM referrals WHERE location_id = ?", (lid,)))
    con.close()
    return {"referrals": data}


@app.get("/api/usage")
def usage(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    con = connect()
    data = rows(
        con.execute(
            "SELECT * FROM usage WHERE location_id = ? ORDER BY created_at DESC LIMIT 50",
            (lid,),
        )
    )
    totals = row(
        con.execute(
            "SELECT COALESCE(SUM(cost),0) cost, COALESCE(SUM(charged),0) charged, COALESCE(SUM(units),0) units FROM usage WHERE location_id = ?",
            (lid,),
        ).fetchone()
    )
    con.close()
    s = settings()
    return {
        "items": data,
        "totals": totals,
        "profit": round((totals["charged"] or 0) - (totals["cost"] or 0), 4),
        "sms_cost": SMS_COST,
        "rebill_sms": s["rebill_sms"],
        "rebill_ai": s["rebill_ai"],
        "example": {
            "texts": 1000,
            "cost": round(1000 * SMS_COST, 4),
            "charged": round(1000 * SMS_COST * s["rebill_sms"], 4),
            "profit": round(1000 * SMS_COST * (s["rebill_sms"] - 1), 4),
        },
    }


@app.post("/api/agency/rebill")
def set_rebill(request: Request, body: RebillIn):
    u = require(request)
    if u["role"] != "agency":
        raise HTTPException(403, "agency only")
    con = connect()
    con.execute("UPDATE settings SET rebill_sms = ?, rebill_ai = ? WHERE id = 1", (body.rebill_sms, body.rebill_ai))
    con.commit()
    con.close()
    return {"ok": True, "settings": settings()}


@app.post("/api/location/plan")
def set_plan(request: Request, body: PlanIn):
    u = require(request)
    if body.plan not in PLANS:
        raise HTTPException(400, "unknown plan")
    lid = loc_id(u, request)
    con = connect()
    con.execute("UPDATE locations SET plan = ? WHERE id = ?", (body.plan, lid))
    con.commit()
    con.close()
    return {"ok": True, "plan": PLANS[body.plan]}


@app.get("/api/agency/revenue")
def revenue(request: Request):
    u = require(request)
    if u["role"] != "agency":
        raise HTTPException(403, "agency only")
    con = connect()
    locs = rows(con.execute("SELECT id, name, plan, setup_fee, setup_complete FROM locations"))
    usage_all = rows(
        con.execute("SELECT location_id, SUM(cost) cost, SUM(charged) charged FROM usage GROUP BY location_id")
    )
    con.close()
    by = {x["location_id"]: x for x in usage_all}
    items = []
    mrr = 0
    setup = 0
    profit = 0
    for loc in locs:
        price = PLANS[loc["plan"]]["price"]
        mrr += price
        setup += loc["setup_fee"] if loc["setup_complete"] else 0
        urow = by.get(loc["id"]) or {"cost": 0, "charged": 0}
        profit += (urow["charged"] or 0) - (urow["cost"] or 0)
        items.append({**loc, "mrr": price, "usage": urow})
    return {"mrr": mrr, "setup": setup, "usage_profit": round(profit, 2), "locations": items}


@app.get("/api/public/location/{slug}")
def public_location(slug: str):
    con = connect()
    loc = row(con.execute("SELECT * FROM locations WHERE slug = ?", (slug,)).fetchone())
    if not loc:
        con.close()
        raise HTTPException(404, "missing")
    reviews = rows(
        con.execute(
            "SELECT rating, text, source, created_at FROM reviews WHERE location_id = ? AND public = 1 ORDER BY created_at DESC LIMIT 8",
            (loc["id"],),
        )
    )
    avg = con.execute(
        "SELECT COALESCE(AVG(rating),0) a FROM reviews WHERE location_id = ? AND public = 1",
        (loc["id"],),
    ).fetchone()["a"]
    posts = rows(
        con.execute(
            "SELECT body, platform, scheduled_at FROM social_posts WHERE location_id = ? AND status = 'published' ORDER BY id DESC LIMIT 6",
            (loc["id"],),
        )
    )
    con.close()
    return {"location": loc, "reviews": reviews, "avg": round(avg, 2), "posts": posts}


@app.patch("/api/location")
def update_location(request: Request, body: LocationIn):
    u = require(request)
    lid = loc_id(u, request)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"ok": True, "location": location(lid)}
    if "place_id" in data and data["place_id"] and not data.get("google_url"):
        data["google_url"] = places.google_review_url(data["place_id"], None)
    fields = ", ".join(f"{k} = ?" for k in data)
    con = connect()
    con.execute(f"UPDATE locations SET {fields} WHERE id = ?", (*data.values(), lid))
    con.commit()
    con.close()
    return {"ok": True, "location": location(lid)}


@app.get("/api/forms")
def list_forms(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "forms")
    con = connect()
    data = rows(con.execute("SELECT * FROM forms WHERE location_id = ? ORDER BY id DESC", (lid,)))
    con.close()
    return {"forms": data}


@app.post("/api/forms")
def create_form(request: Request, body: FormIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "forms")
    slug = slugify(body.name) + "-" + secrets.token_hex(3)
    con = connect()
    fid = con.execute(
        "INSERT INTO forms (location_id, name, slug, fields, created_at) VALUES (?,?,?,?,?)",
        (lid, body.name.strip(), slug, body.fields.strip(), time.time()),
    ).lastrowid
    con.commit()
    con.close()
    return {"ok": True, "id": fid, "slug": slug, "url": f"/f/{slug}"}


@app.get("/api/public/form/{slug}")
def public_form(slug: str):
    con = connect()
    form = row(
        con.execute(
            """SELECT f.*, l.name AS business FROM forms f
               JOIN locations l ON l.id = f.location_id WHERE f.slug = ?""",
            (slug,),
        ).fetchone()
    )
    con.close()
    if not form:
        raise HTTPException(404, "form missing")
    return form


@app.post("/api/public/form/{slug}")
def public_form_submit(slug: str, body: FormSubmitIn):
    con = connect()
    form = row(con.execute("SELECT * FROM forms WHERE slug = ?", (slug,)).fetchone())
    if not form:
        con.close()
        raise HTTPException(404, "form missing")
    text = "\n".join(f"{k}: {v}" for k, v in (body.payload or {}).items())
    name = str((body.payload or {}).get("name") or "Form")
    tid = con.execute(
        """INSERT INTO threads (location_id, customer_id, channel, name, status, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (form["location_id"], None, "form", name, "open", time.time()),
    ).lastrowid
    con.execute(
        "INSERT INTO messages (thread_id, direction, body, created_at) VALUES (?,?,?,?)",
        (tid, "in", text or "(empty)", time.time()),
    )
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/referrals")
def add_referral(request: Request, body: ReferralIn):
    u = require(request)
    lid = loc_id(u, request)
    gated(location(lid), "referrals")
    code = secrets.token_urlsafe(6)
    con = connect()
    rid = con.execute(
        "INSERT INTO referrals (location_id, name, email, code, status, created_at) VALUES (?,?,?,?,?,?)",
        (lid, body.name.strip(), body.email.strip(), code, "invited", time.time()),
    ).lastrowid
    loc = row(con.execute("SELECT slug, name FROM locations WHERE id = ?", (lid,)).fetchone())
    con.commit()
    con.close()
    link = absolute_link(f"/app?ref={code}", request)
    sent = None
    if body.email.strip():
        sent = deliver.send_email(
            body.email.strip(),
            f"{loc['name']} invited you to Starling",
            f"{u['name']} at {loc['name']} referred you. Open {link} to start.",
        )
        log_delivery(lid, "email", body.email.strip(), link, sent)
    return {"ok": True, "id": rid, "code": code, "link": link, "delivery": sent}


@app.post("/api/ask")
async def ask_ai(request: Request):
    u = require(request)
    lid = loc_id(u, request)
    loc = location(lid)
    gated(loc, "reviews")
    payload = await request.json()
    q = str(payload.get("q") or "").strip()
    if not q:
        raise HTTPException(400, "empty")
    text = await grok.complete(
        f"You are Starling Ask AI for {loc['name']}, a {loc['industry']} in {loc['city']}. Answer: {q}",
        "Be concise. You help local operators with reviews, listings, and messaging.",
    )
    meter(lid, "ai", 1)
    return {"ok": True, "text": text or "Ask AI needs a Grok key — set XAI_API_KEY."}

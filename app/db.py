from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("STARLING_DB") or ROOT / "data" / "starling.db")
PEPPER = os.environ.get("STARLING_PEPPER", "starling-local-pepper")

PLANS = {
    "starter": {
        "name": "Starter",
        "price": 349,
        "features": ["reviews", "listings", "inbox", "chat", "forms", "pages", "workflows"],
    },
    "growth": {
        "name": "Growth",
        "price": 599,
        "features": [
            "reviews",
            "listings",
            "inbox",
            "chat",
            "forms",
            "pages",
            "workflows",
            "social",
            "referrals",
        ],
    },
    "dominate": {
        "name": "Dominate",
        "price": 699,
        "features": [
            "reviews",
            "listings",
            "inbox",
            "chat",
            "forms",
            "pages",
            "workflows",
            "social",
            "referrals",
            "ai_studio",
            "agents",
        ],
    },
}

SMS_COST = 0.0083


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def hash_pw(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), (salt + PEPPER).encode(), 120_000)
    return f"{salt}${digest.hex()}"


def check_pw(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), (salt + PEPPER).encode(), 120_000).hex()
    return hmac.compare_digest(test, digest)


def init() -> None:
    con = connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            location_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            industry TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            website TEXT,
            plan TEXT NOT NULL DEFAULT 'starter',
            setup_fee INTEGER NOT NULL DEFAULT 1500,
            setup_complete INTEGER NOT NULL DEFAULT 0,
            google_url TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            rebill_sms REAL NOT NULL DEFAULT 2.0,
            rebill_ai REAL NOT NULL DEFAULT 1.5,
            brand TEXT NOT NULL DEFAULT 'Starling'
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT
        );
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            at TEXT NOT NULL,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS review_requests (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            customer_id INTEGER,
            source TEXT NOT NULL,
            rating INTEGER NOT NULL,
            text TEXT,
            public INTEGER NOT NULL DEFAULT 1,
            reply TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            customer_id INTEGER,
            channel TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            thread_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            directory TEXT NOT NULL,
            status TEXT NOT NULL,
            nap_match INTEGER NOT NULL,
            last_sync REAL
        );
        CREATE TABLE IF NOT EXISTS social_posts (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            platform TEXT NOT NULL,
            status TEXT NOT NULL,
            scheduled_at REAL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            trigger TEXT NOT NULL,
            action TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run REAL
        );
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            units INTEGER NOT NULL,
            cost REAL NOT NULL,
            charged REAL NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            code TEXT UNIQUE,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS forms (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            fields TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demos (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            business TEXT,
            phone TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY,
            location_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT,
            body TEXT,
            ok INTEGER NOT NULL,
            error TEXT,
            created_at REAL NOT NULL
        );
        """
    )
    cols = {r["name"] for r in con.execute("PRAGMA table_info(locations)")}
    for col, spec in {
        "lat": "REAL",
        "lng": "REAL",
        "place_id": "TEXT",
        "description": "TEXT",
        "referral_code": "TEXT",
    }.items():
        if col not in cols:
            con.execute(f"ALTER TABLE locations ADD COLUMN {col} {spec}")
    lcols = {r["name"] for r in con.execute("PRAGMA table_info(listings)")}
    if "url" not in lcols:
        con.execute("ALTER TABLE listings ADD COLUMN url TEXT")
    if "detail" not in lcols:
        con.execute("ALTER TABLE listings ADD COLUMN detail TEXT")
    rcols = {r["name"] for r in con.execute("PRAGMA table_info(reviews)")}
    if "reply_draft" not in rcols:
        con.execute("ALTER TABLE reviews ADD COLUMN reply_draft TEXT")
    if con.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        con.execute(
            "INSERT INTO settings (id, rebill_sms, rebill_ai, brand) VALUES (1, 2.0, 1.5, 'Starling')"
        )
    con.commit()
    con.close()


def slugify(name: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in raw:
        raw = raw.replace("--", "-")
    return (raw or "location")[:48]


def create_location(con: sqlite3.Connection, **kw) -> int:
    slug = kw.get("slug") or slugify(kw["name"])
    base = slug
    n = 1
    while con.execute("SELECT id FROM locations WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    code = secrets.token_urlsafe(6)
    lid = con.execute(
        """INSERT INTO locations
           (name, slug, industry, phone, email, address, city, state, zip, website, plan, setup_fee, setup_complete, google_url, place_id, referral_code)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)""",
        (
            kw["name"],
            slug,
            kw.get("industry") or "local",
            kw.get("phone") or "",
            kw.get("email") or "",
            kw.get("address") or "",
            kw.get("city") or "",
            kw.get("state") or "",
            kw.get("zip") or "",
            kw.get("website") or "",
            kw.get("plan") or "starter",
            int(kw.get("setup_fee") or 1500),
            kw.get("google_url") or "",
            kw.get("place_id") or "",
            code,
        ),
    ).lastrowid
    for name, trigger, action in [
        ("After visit → review request", "visit.created", "review.request"),
        ("1–3 star → private ticket", "review.low", "inbox.flag"),
        ("Weekly social draft", "weekly", "social.draft"),
    ]:
        con.execute(
            "INSERT INTO workflows (location_id, name, trigger, action, enabled) VALUES (?,?,?,?,1)",
            (lid, name, trigger, action),
        )
    return lid


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]

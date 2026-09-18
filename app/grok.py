from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("GROK_MODEL", "grok-4.20-0309-non-reasoning")


def resolve_api_key() -> str | None:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    auth = Path.home() / ".grok" / "auth.json"
    if not auth.exists():
        return None
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
        for entry in data.values() if isinstance(data, dict) else []:
            token = str((entry or {}).get("key") or "").strip()
            if token:
                return token
    except (OSError, json.JSONDecodeError):
        return None
    return None


def has_key() -> bool:
    return bool(resolve_api_key())


async def complete(prompt: str, system: str = "") -> str:
    key = resolve_api_key()
    if not key:
        raise RuntimeError("XAI_API_KEY is missing")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages, "temperature": 0.4},
        )
        r.raise_for_status()
        return (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""


async def review_reply(business: str, industry: str, rating: int, text: str) -> str:
    out = await complete(
        f"Business: {business} ({industry})\nRating: {rating}/5\nReview: {text or '(no text)'}\n"
        "Write a short, warm, professional public reply (2-4 sentences). No hashtags.",
        "You write review replies for local businesses. Be specific, human, and brand-safe.",
    )
    if not out:
        raise RuntimeError("Grok returned an empty review reply")
    return out.strip()


async def review_request_copy(business: str, customer: str) -> str:
    out = await complete(
        f"Write a short SMS (max 240 chars) from {business} to {customer} asking for a Google-style review after a visit. Friendly, not salesy. Include a placeholder {{link}}.",
        "You write SMS review requests for local businesses.",
    )
    if not out:
        return f"Hi {customer.split()[0]}, it's {business}. Thanks for coming in — if we earned it, would you share a quick review? {{link}}"
    return out.strip()


async def listing_description(business: str, industry: str, city: str) -> str:
    out = await complete(
        f"Write a 420-character Google Business description for {business}, a {industry} in {city}. Include services, neighborhood, and a CTA. No quotes.",
        "You write local SEO listing copy.",
    )
    if not out:
        raise RuntimeError("Grok returned an empty listing description")
    return out.strip()[:750]


async def social_post(business: str, industry: str, topic: str) -> str:
    out = await complete(
        f"Write one social post for {business} ({industry}). Topic: {topic or 'this week at the shop'}. 400 chars max, 1-2 emojis max, no hashtag spam.",
        "You write local business social posts.",
    )
    if not out:
        raise RuntimeError("Grok returned an empty social post")
    return out.strip()


async def inbox_reply(business: str, channel: str, incoming: str) -> str:
    out = await complete(
        f"Business: {business}\nChannel: {channel}\nCustomer said: {incoming}\nWrite a concise reply the front desk can send.",
        "You draft customer-service replies for a local business inbox.",
    )
    if not out:
        raise RuntimeError("Grok returned an empty inbox reply")
    return out.strip()

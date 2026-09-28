"""Instant Local Reply Pack — free, zero-API review reply drafts for Starling.

Idea inspiration (no code copied): local-first free pipelines that work without an
LLM key, as in haqaliz/rereflect (MIT, https://github.com/haqaliz/rereflect).
Aspect cues reuse Starling's own VADER-backed insights analyzer
(cjhutto/vaderSentiment, MIT).

All reply text here is original Starling template copy, parameterized by business
name, industry, star rating, and mentioned themes (staff, wait, price, …).
"""
from __future__ import annotations

from typing import Any

from app import insights

PACK = "instant-pack-v1"

# Industry noun used in a couple of praise lines ("your dental team").
_INDUSTRY_TEAM: dict[str, str] = {
    "dental": "dental team",
    "dentist": "dental team",
    "medspa": "medspa team",
    "salon": "salon team",
    "spa": "spa team",
    "auto": "shop team",
    "auto repair": "shop team",
    "restaurant": "kitchen and front-of-house",
    "cafe": "cafe team",
    "vet": "vet team",
    "veterinary": "vet team",
    "fitness": "studio team",
    "gym": "gym team",
    "legal": "office",
    "law": "office",
    "hvac": "crew",
    "plumbing": "crew",
    "home services": "crew",
}

_ASPECT_PRAISE: dict[str, str] = {
    "staff": "We'll keep that warm, personal service front and center.",
    "wait": "On-time visits matter to us — glad that came through.",
    "price": "Fair, clear pricing is something we work hard on.",
    "quality": "We're proud of the work — thank you for noticing.",
    "clean": "A clean, comfortable space is non-negotiable here.",
    "communication": "Clear updates are part of how we take care of people.",
    "booking": "Easy scheduling is something we'll keep improving.",
    "place": "Glad the location worked for you.",
}

_ASPECT_RECOVERY: dict[str, str] = {
    "staff": "We're coaching the team so every visit feels respectful and helpful.",
    "wait": "We're tightening the schedule so waits don't stretch like that again.",
    "price": "Billing should never be a surprise — please message us and we'll review your account.",
    "quality": "We want another look at the work so we can make this right.",
    "clean": "We're rechecking our cleaning checklist today.",
    "communication": "You should have heard from us sooner — we're fixing our follow-up process.",
    "booking": "We're reviewing how appointments get booked and confirmed.",
    "place": "We've noted the location/parking feedback for the team.",
}


def _team_phrase(industry: str) -> str:
    key = (industry or "").strip().lower()
    return _INDUSTRY_TEAM.get(key, "team")


def _first_name_or_guest(customer: str | None) -> str:
    raw = (customer or "").strip()
    if not raw:
        return "there"
    return raw.split()[0]


def _aspect_keys(text: str, rating: int) -> list[str]:
    analysis = insights.analyze_review(text or "", float(rating))
    keys: list[str] = []
    for m in analysis.get("mentions") or []:
        a = m.get("aspect")
        if a and a not in keys:
            keys.append(a)
    return keys[:3]


def _band(rating: int) -> str:
    if rating >= 4:
        return "praise"
    if rating == 3:
        return "mixed"
    return "recovery"


def draft_review_reply(
    business: str,
    industry: str,
    rating: int,
    text: str = "",
    customer: str | None = None,
) -> dict[str, Any]:
    """Return an instant local reply draft. Never calls an external API."""
    biz = (business or "our team").strip() or "our team"
    rating = max(1, min(5, int(rating or 3)))
    band = _band(rating)
    aspects = _aspect_keys(text, rating)
    guest = _first_name_or_guest(customer)
    team = _team_phrase(industry)
    aspect_line = ""
    if aspects:
        table = _ASPECT_PRAISE if band == "praise" else _ASPECT_RECOVERY
        # For mixed, prefer recovery line if any negative-leaning aspect, else praise.
        if band == "mixed":
            table = _ASPECT_RECOVERY if aspects else _ASPECT_PRAISE
        aspect_line = table.get(aspects[0], "")

    if band == "praise":
        closer = aspect_line or "We hope to see you again soon."
        body = (
            f"Hi {guest} — thank you for the {rating}★ review of {biz}. "
            f"Notes like yours mean a lot to our {team}. "
            f"{closer}"
        )
    elif band == "mixed":
        closer = aspect_line or "If there is anything we can fix, reply here and we will follow up."
        body = (
            f"Hi {guest} — thank you for sharing honest feedback with {biz}. "
            f"A {rating}★ visit tells us we were close, and we want the next one to feel better. "
            f"{closer}"
        )
    else:
        closer = aspect_line or "Please reach out so we can make this right."
        body = (
            f"Hi {guest} — we are sorry your visit with {biz} fell short. "
            f"A {rating}★ experience is not the standard we hold ourselves to. "
            f"{closer} "
            f"We would welcome another chance."
        )

    # Keep public replies short (2–4 sentences) and strip double spaces.
    text_out = " ".join(body.split())
    return {
        "text": text_out,
        "source": PACK,
        "band": band,
        "aspects": aspects,
        "metered": False,
    }


def draft_inbox_reply(business: str, channel: str, incoming: str) -> dict[str, Any]:
    """Short front-desk draft for inbox threads — still free/local."""
    biz = (business or "our team").strip() or "our team"
    channel = (channel or "message").strip().lower()
    incoming = (incoming or "").strip()
    analysis = insights.analyze_review(incoming, None)
    sentiment = analysis.get("sentiment") or "neutral"
    aspects = [m["aspect"] for m in (analysis.get("mentions") or []) if m.get("aspect")]
    aspects = list(dict.fromkeys(aspects))[:2]

    if sentiment == "negative" or (aspects and any(
        m.get("score", 0) <= -0.35 for m in (analysis.get("mentions") or [])
    )):
        tip = _ASPECT_RECOVERY.get(aspects[0], "We want to make this right.") if aspects else "We want to make this right."
        text = (
            f"Thanks for writing to {biz}. We're sorry this happened. "
            f"{tip} A teammate will follow up on this {channel} shortly."
        )
    elif sentiment == "positive":
        tip = _ASPECT_PRAISE.get(aspects[0], "We're glad we could help.") if aspects else "We're glad we could help."
        text = f"Thanks for reaching out to {biz}! {tip} Let us know if you need anything else."
    else:
        text = (
            f"Thanks for contacting {biz}. We've got your {channel} and will reply with details shortly. "
            f"If this is urgent, call the front desk and mention this message."
        )
    return {
        "text": " ".join(text.split()),
        "source": PACK,
        "band": sentiment,
        "aspects": aspects,
        "metered": False,
    }

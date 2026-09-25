"""Review Insights: local, free aspect-based sentiment for customer reviews.

Sentence-level sentiment uses VADER (cjhutto/vaderSentiment, MIT,
https://github.com/cjhutto/vaderSentiment), a lexicon + rule model built for
short social text. On top of it this module adds (original code):

* an aspect lexicon for local businesses (staff, wait time, price, results ...),
* complaint patterns VADER misses ("waited 45 minutes", "never called back"),
* rating/text mismatch flags (a 5-star rating with an angry comment, etc.),
* a "fix first" / "keep doing" summary and a 30-day trend.

No paid API and no network calls: runs in-process on every request.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any, Iterable

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()
# Domain words VADER's general lexicon scores as neutral or not at all.
_analyzer.lexicon.update(
    {
        "painless": 2.2,
        "spotless": 2.0,
        "gentle": 1.6,
        "overcharged": -2.6,
        "overpriced": -2.2,
        "rip-off": -2.6,
        "ripoff": -2.6,
        "unprofessional": -2.3,
        "dismissive": -2.0,
        "rushed": -1.6,
        "filthy": -2.6,
        "late": -1.0,
        "understaffed": -1.8,
        "knowledgeable": 1.8,
        "thorough": 1.7,
        "prompt": 1.3,
        "responsive": 1.5,
        "unresponsive": -2.0,
    }
)

ASPECTS: dict[str, dict[str, Any]] = {
    "staff": {
        "label": "Staff & service",
        "words": r"staff|team|receptionist|front desk|reception|doctor|dr\.?|dentist|hygienist|nurse|tech|technician|"
        r"manager|owner|employee|server|waiter|waitress|stylist|mechanic|service|friendly|rude|polite|attitude|"
        r"professional|unprofessional|kind|helpful|courteous",
    },
    "wait": {
        "label": "Wait time",
        "words": r"wait|waited|waiting|late|on time|delay|delayed|quick|quickly|fast|slow|prompt|minutes|hour|hours|"
        r"forever|punctual|rushed",
    },
    "price": {
        "label": "Price & billing",
        "words": r"price|prices|pricing|cost|costs|expensive|cheap|affordable|overpriced|overcharged|bill|billed|billing|"
        r"invoice|insurance|charge|charged|fee|fees|value|deal|refund|quote|estimate",
    },
    "quality": {
        "label": "Quality of work",
        "words": r"result|results|quality|job|work|repair|repaired|fixed|treatment|cleaning|procedure|haircut|food|"
        r"meal|taste|painless|pain|thorough|careful|botched|mistake|redo",
    },
    "clean": {
        "label": "Cleanliness",
        "words": r"clean|cleanliness|dirty|filthy|spotless|smell|smelled|hygiene|hygienic|tidy|messy|sanitary",
    },
    "communication": {
        "label": "Communication",
        "words": r"call|called|callback|call back|phone|email|text|texted|explain|explained|answer|answered|respond|"
        r"responded|responsive|unresponsive|reminder|update|updates|informed|listened|listen",
    },
    "booking": {
        "label": "Booking & scheduling",
        "words": r"book|booked|booking|appointment|appointments|schedule|scheduled|scheduling|reschedule|rescheduled|"
        r"cancel|cancelled|canceled|availability|online booking|walk-in",
    },
    "place": {
        "label": "Location & parking",
        "words": r"parking|park|location|located|easy to find|access|accessible|waiting room|lobby|office|atmosphere|"
        r"comfortable|cozy|decor",
    },
}
_ASPECT_RX = {k: re.compile(rf"\b(?:{v['words']})\b", re.I) for k, v in ASPECTS.items()}

# Complaint phrasings VADER scores neutral: force a negative reading.
_COMPLAINTS: list[tuple[str, re.Pattern[str]]] = [
    ("wait", re.compile(r"\bwait(?:ed|ing)?\s+(?:for\s+)?(?:over|almost|about|nearly|like|more than)?\s*(?:an?\s+)?(?:\d+\s*)?(?:min|mins|minutes|hour|hours|hr|hrs|forever)\b", re.I)),
    ("communication", re.compile(r"\bnever\s+(?:called|call|got|get|heard|responded|answered)\b|\bno\s+(?:call|callback|response|reply)\b|\bdidn'?t\s+(?:call|answer|respond)\b", re.I)),
    ("price", re.compile(r"\bhidden\s+(?:fee|fees|charge|charges|cost|costs)\b|\bsurprise\s+(?:bill|charge|fee)\b|\bcharged\s+(?:me\s+)?(?:twice|double|extra)\b", re.I)),
    ("booking", re.compile(r"\b(?:cancel(?:l)?ed|rescheduled)\s+(?:on\s+me|my\s+appointment|last\s+minute)\b|\bdouble[- ]booked\b", re.I)),
]

_STOP = set(
    """a an the and or but if then so to of in on at for with from by as is are was were be been being it its this that
    these those i me my we our you your he she they them their his her there here very really just also too not no
    have has had do did does done can could would should will im i'm ive i've it's dont don't didnt didn't
    all any some more most much many one two get got go went come came us out up down over again what when
    who how than about into after before because while which only even still ever every back well""".split()
)


_ABBR = re.compile(r"\b(Dr|Mr|Mrs|Ms|St|Jr|Sr|vs|etc|approx)\.", re.I)


def _sentences(text: str) -> list[str]:
    text = _ABBR.sub(lambda m: m.group(1) + "\u2024", text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s+|\s+but\s+", text or "", flags=re.I)
    return [p.strip().replace("\u2024", ".") for p in parts if p and p.strip()]


def _label(compound: float) -> str:
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


def analyze_review(text: str, rating: float | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    overall = _analyzer.polarity_scores(text)["compound"] if text else 0.0
    mentions: list[dict[str, Any]] = []
    for sent in _sentences(text):
        comp = _analyzer.polarity_scores(sent)["compound"]
        forced = {k for k, rx in _COMPLAINTS if rx.search(sent)}
        if forced and comp > -0.3:
            comp = min(comp, 0.0) - 0.4
        found = {k for k, rx in _ASPECT_RX.items() if rx.search(sent)} | forced
        for aspect in sorted(found):
            mentions.append({"aspect": aspect, "sentiment": _label(comp), "score": round(comp, 3), "quote": sent[:220]})
    if any(m["score"] < -0.3 for m in mentions) and overall > 0.3:
        overall = min(overall, 0.2)  # mixed review: do not let one glowing line hide a complaint
    mismatch = None
    if rating is not None and text:
        if rating >= 4 and overall <= -0.3:
            mismatch = "high rating, negative words"
        elif rating >= 4 and any(m["score"] <= -0.35 for m in mentions):
            mismatch = "high rating, but a complaint inside"
        elif rating <= 2 and overall >= 0.5:
            mismatch = "low rating, positive words"
    return {"sentiment": _label(overall), "score": round(overall, 3), "mentions": mentions, "mismatch": mismatch}


def _keywords(texts: Iterable[str], limit: int = 12) -> list[dict[str, Any]]:
    grams: Counter[str] = Counter()
    for text in texts:
        words = [w for w in re.findall(r"[a-z][a-z'\-]+", (text or "").lower())]
        clean = [w for w in words if w not in _STOP and len(w) > 2]
        grams.update(set(clean))
        for a, b in zip(words, words[1:]):
            if a not in _STOP and b not in _STOP and len(a) > 2 and len(b) > 2:
                grams[f"{a} {b}"] += 1
    common = [(k, v) for k, v in grams.most_common(120) if v >= 2]
    bigrams = [(k, v) for k, v in common if " " in k]
    picked: list[dict[str, Any]] = []
    covered: dict[str, int] = {}
    for k, v in bigrams[: limit // 2]:
        picked.append({"term": k, "count": v})
        for w in k.split():
            covered[w] = max(covered.get(w, 0), v)
    for k, v in common:
        if len(picked) >= limit:
            break
        if " " in k or (k in covered and v <= covered[k] * 1.25):
            continue
        picked.append({"term": k, "count": v})
    picked.sort(key=lambda p: -p["count"])
    return picked


def summarize(reviews: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """reviews: [{id?, text, rating?, created_at?, public?}] -> insight report."""
    now = now or time.time()
    per: list[dict[str, Any]] = []
    for r in reviews:
        a = analyze_review(r.get("text") or "", r.get("rating"))
        per.append({**a, "id": r.get("id"), "rating": r.get("rating"), "created_at": r.get("created_at"),
                    "public": r.get("public"), "text": (r.get("text") or "")[:400]})
    with_text = [p for p in per if p["text"]]
    agg: dict[str, dict[str, Any]] = {}
    for p in per:
        for m in p["mentions"]:
            row = agg.setdefault(m["aspect"], {"aspect": m["aspect"], "label": ASPECTS[m["aspect"]]["label"],
                                               "positive": 0, "negative": 0, "neutral": 0, "mentions": 0,
                                               "praise": [], "complaints": []})
            row[m["sentiment"]] += 1
            row["mentions"] += 1
            bucket = "praise" if m["sentiment"] == "positive" else "complaints" if m["sentiment"] == "negative" else None
            if bucket and len(row[bucket]) < 3 and m["quote"] not in row[bucket]:
                row[bucket].append(m["quote"])
    aspects = []
    for row in agg.values():
        row["net"] = round((row["positive"] - row["negative"]) / row["mentions"], 2) if row["mentions"] else 0
        aspects.append(row)
    aspects.sort(key=lambda r: (-r["mentions"], r["label"]))
    fix = sorted([a for a in aspects if a["negative"]], key=lambda a: (-a["negative"], a["net"]))
    keep = sorted([a for a in aspects if a["positive"]], key=lambda a: (-a["positive"], -a["net"]))

    def window(lo: float, hi: float) -> dict[str, Any]:
        xs = [p for p in with_text if p.get("created_at") and lo <= float(p["created_at"]) < hi]
        return {"reviews": len(xs), "avg_sentiment": round(sum(p["score"] for p in xs) / len(xs), 3) if xs else None}

    day = 86400
    counts = Counter(p["sentiment"] for p in with_text)
    return {
        "reviews": len(per),
        "with_text": len(with_text),
        "sentiment": {k: counts.get(k, 0) for k in ("positive", "neutral", "negative")},
        "avg_sentiment": round(sum(p["score"] for p in with_text) / len(with_text), 3) if with_text else None,
        "aspects": aspects,
        "fix_first": fix[0]["aspect"] if fix else None,
        "keep_doing": keep[0]["aspect"] if keep else None,
        "keywords": _keywords(p["text"] for p in with_text),
        "mismatches": [
            {"id": p["id"], "rating": p["rating"], "flag": p["mismatch"], "text": p["text"][:200]}
            for p in per if p["mismatch"]
        ][:10],
        "trend": {"last_30d": window(now - 30 * day, now + 1), "prev_30d": window(now - 60 * day, now - 30 * day)},
        "engine": "VADER (cjhutto/vaderSentiment, MIT) + Starling aspect lexicon",
    }

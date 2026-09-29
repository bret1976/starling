"""NAP Diff Pack — free, local Name/Address/Phone consistency grading for Starling.

Idea inspiration (no code copied): smart citation diffs that treat formatting-only
phone/address variants as safe while flagging real mismatches, as in
bensblueprints/local-seo-citation-tracker-mvp / Citewatch
(MIT, https://github.com/bensblueprints/local-seo-citation-tracker-mvp).

All normalization tables, grading rules, and API shapes here are original
Starling Python. No Node source, Electron UI, or directory seed lists vendored.
"""
from __future__ import annotations

import json
import re
from typing import Any

PACK = "nap-diff-v1"

# USPS-style street / unit / directional shortenings for address compare.
_STREET_ABBREV: dict[str, str] = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "square": "sq",
    "terrace": "ter",
    "highway": "hwy",
    "parkway": "pkwy",
    "circle": "cir",
    "trail": "trl",
    "suite": "ste",
    "apartment": "apt",
    "building": "bldg",
    "floor": "fl",
    "department": "dept",
    "room": "rm",
    "number": "no",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}

_FieldGrade = str  # match | formatting | mismatch | missing | skipped
_Overall = str  # match | formatting | incomplete | mismatch


def normalize_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_name(raw: str | None) -> str:
    s = str(raw or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_address(raw: str | None) -> str:
    s = str(raw or "").lower()
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"[^a-z0-9\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = [_STREET_ABBREV.get(tok, tok) for tok in s.split(" ") if tok]
    return " ".join(parts)


def _compare_field(canonical: str | None, observed: str | None, kind: str) -> _FieldGrade:
    can = str(canonical or "").strip()
    obs = str(observed or "").strip()
    if not obs:
        return "missing"
    if can == obs:
        return "match"
    if kind == "phone":
        n_can, n_obs = normalize_phone(can), normalize_phone(obs)
        if not n_can or not n_obs:
            return "mismatch" if n_can != n_obs else "missing"
        return "formatting" if n_can == n_obs else "mismatch"
    if kind == "address":
        return "formatting" if normalize_address(can) == normalize_address(obs) else "mismatch"
    # name / default
    return "formatting" if normalize_name(can) == normalize_name(obs) else "mismatch"


def _overall(grades: list[_FieldGrade]) -> _Overall:
    active = [g for g in grades if g != "skipped"]
    if not active:
        return "incomplete"
    if "mismatch" in active:
        return "mismatch"
    if "missing" in active:
        return "incomplete"
    if "formatting" in active:
        return "formatting"
    return "match"


def nap_match_flag(overall: _Overall) -> int:
    """Compatibility with Starling listings.nap_match INTEGER (1 = ok enough)."""
    return 1 if overall in ("match", "formatting") else 0


def grade_nap(
    canonical: dict[str, Any],
    observed: dict[str, Any],
    *,
    fields: tuple[str, ...] = ("name", "address", "phone"),
) -> dict[str, Any]:
    """Grade observed NAP against canonical.

    `canonical` / `observed` keys: name, address, phone (optional city/state/zip
    folded into address when building the observed address elsewhere).
    """
    field_out: dict[str, dict[str, str]] = {}
    grades: list[_FieldGrade] = []
    for key in ("name", "address", "phone"):
        if key not in fields:
            field_out[key] = {
                "canonical": str(canonical.get(key) or ""),
                "observed": str(observed.get(key) or ""),
                "status": "skipped",
            }
            grades.append("skipped")
            continue
        status = _compare_field(canonical.get(key), observed.get(key), key)
        field_out[key] = {
            "canonical": str(canonical.get(key) or ""),
            "observed": str(observed.get(key) or ""),
            "status": status,
        }
        grades.append(status)
    overall = _overall(grades)
    return {
        "pack": PACK,
        "overall": overall,
        "nap_match": nap_match_flag(overall),
        "fields": field_out,
        "fix_hint": _fix_hint(overall, field_out),
    }


def _fix_hint(overall: _Overall, fields: dict[str, dict[str, str]]) -> str | None:
    if overall == "match":
        return None
    if overall == "formatting":
        return "Formatting-only differences — safe to leave; directories often rewrite phones and Street→St."
    bad = [k for k, v in fields.items() if v.get("status") in ("mismatch", "missing") and v.get("status") != "skipped"]
    if overall == "incomplete":
        missing = [k for k, v in fields.items() if v.get("status") == "missing"]
        if missing:
            return f"Listing is missing: {', '.join(missing)}."
        return "Listing is incomplete versus your canonical NAP."
    if bad:
        return f"Real mismatch on: {', '.join(bad)} — fix the directory so local search stays consistent."
    return "NAP does not match your canonical profile."


def build_address_line(parts: dict[str, Any]) -> str:
    """Join address / city / state / zip into one compare string."""
    chunks = [
        str(parts.get("address") or "").strip(),
        str(parts.get("city") or "").strip(),
        str(parts.get("state") or "").strip(),
        str(parts.get("zip") or parts.get("postcode") or "").strip(),
    ]
    return ", ".join(c for c in chunks if c)


def detail_payload(grade: dict[str, Any], *, label: str | None = None, extra: dict | None = None) -> str:
    """Serialize grade into listings.detail JSON (backward-compatible string)."""
    payload: dict[str, Any] = {
        "pack": PACK,
        "overall": grade.get("overall"),
        "nap_match": grade.get("nap_match"),
        "fix_hint": grade.get("fix_hint"),
        "fields": grade.get("fields"),
    }
    if label:
        payload["label"] = label
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def parse_detail(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text.startswith("{"):
        return {"label": text, "pack": None}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"label": text}
    except json.JSONDecodeError:
        return {"label": text, "pack": None}


def summarize(listings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"match": 0, "formatting": 0, "incomplete": 0, "mismatch": 0, "unknown": 0}
    for row in listings:
        parsed = parse_detail(row.get("detail"))
        overall = (parsed or {}).get("overall") if parsed else None
        if overall in counts:
            counts[overall] += 1
        else:
            counts["unknown"] += 1
    return {"pack": PACK, "counts": counts}

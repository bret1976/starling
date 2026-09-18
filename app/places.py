from __future__ import annotations

import httpx

UA = {"User-Agent": "Starling/1.0 (local listings; https://starling.local)"}


def google_review_url(place_id: str | None, fallback: str | None) -> str | None:
    if place_id:
        return f"https://search.google.com/local/writereview?placeid={place_id}"
    return fallback or None


def scan_nap(name: str, address: str, city: str, state: str, zipc: str) -> dict:
    q = ", ".join(p for p in [name, address, city, state, zipc] if p)
    if not q.strip():
        return {"ok": False, "error": "address required"}
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 3, "addressdetails": 1},
            headers=UA,
            timeout=20.0,
        )
        r.raise_for_status()
        hits = r.json() or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "hits": []}
    if not hits:
        return {"ok": True, "found": False, "lat": None, "lng": None, "label": None, "hits": []}
    top = hits[0]
    return {
        "ok": True,
        "found": True,
        "lat": float(top.get("lat")),
        "lng": float(top.get("lon")),
        "label": top.get("display_name"),
        "osm_id": top.get("osm_id"),
        "hits": [{"label": h.get("display_name"), "lat": h.get("lat"), "lng": h.get("lon")} for h in hits],
    }

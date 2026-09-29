from __future__ import annotations

import httpx

UA = {"User-Agent": "Starling/1.0 (local listings; https://starling.local)"}


def google_review_url(place_id: str | None, fallback: str | None) -> str | None:
    if place_id:
        return f"https://search.google.com/local/writereview?placeid={place_id}"
    return fallback or None


def _address_from_nominatim(hit: dict) -> dict:
    addr = hit.get("address") or {}
    road_parts = [
        str(addr.get("house_number") or "").strip(),
        str(addr.get("road") or addr.get("pedestrian") or addr.get("footway") or "").strip(),
    ]
    street = " ".join(p for p in road_parts if p)
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("municipality")
        or ""
    )
    return {
        "name": (hit.get("name") or addr.get("amenity") or addr.get("shop") or addr.get("office") or "").strip()
        or None,
        "address": street or None,
        "city": str(city).strip() or None,
        "state": str(addr.get("state") or "").strip() or None,
        "zip": str(addr.get("postcode") or "").strip() or None,
        "country": str(addr.get("country_code") or "").strip().upper() or None,
    }


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
        return {"ok": True, "found": False, "lat": None, "lng": None, "label": None, "hits": [], "components": None}
    top = hits[0]
    components = _address_from_nominatim(top)
    return {
        "ok": True,
        "found": True,
        "lat": float(top.get("lat")),
        "lng": float(top.get("lon")),
        "label": top.get("display_name"),
        "osm_id": top.get("osm_id"),
        "components": components,
        "hits": [
            {
                "label": h.get("display_name"),
                "lat": h.get("lat"),
                "lng": h.get("lon"),
                "components": _address_from_nominatim(h),
            }
            for h in hits
        ],
    }

"""Security headers, HEAD support, robots.txt and sitemap.xml for Starling.

Backend only: no markup, styles or visuals change.
"""
from __future__ import annotations

from app import deliver

PUBLIC_PAGES = [
    "/", "/products", "/solutions", "/industries", "/customers",
    "/resources", "/pricing", "/demo", "/review-analyzer",
]

# Pages use inline <script>, inline style attributes and Google Fonts.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https:",
    "connect-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

SECURITY_HEADERS = {
    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
    b"content-security-policy": CSP.encode(),
    b"x-frame-options": b"SAMEORIGIN",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
}


class HardeningMiddleware:
    """ASGI middleware: adds security headers; answers HEAD like GET without a body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_head = scope.get("method") == "HEAD"
        if is_head:
            scope = dict(scope, method="GET")

        async def _send(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                for k, v in SECURITY_HEADERS.items():
                    if k not in existing:
                        headers.append((k, v))
                message = dict(message, headers=headers)
            elif message["type"] == "http.response.body" and is_head:
                message = dict(message, body=b"")
            await send(message)

        await self.app(scope, receive, _send)


def robots_txt() -> str:
    base = deliver.public_url()
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /app\n"
        "Disallow: /api/\n"
        "Disallow: /r/\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def sitemap_xml() -> str:
    base = deliver.public_url()
    urls = "".join(f"  <url><loc>{base}{p if p != '/' else '/'}</loc></url>\n" for p in PUBLIC_PAGES)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )

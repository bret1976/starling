# Starling

Local-business SaaS from the Birdeye model in [I Cloned a $500M Software Company (For Free)](https://www.youtube.com/watch?v=wiXG8rRAbes).

Reviews AI, listings, unified inbox, social planner, automations, pages/chat, SMS rebilling, and agency MRR.

## Demo

- Location: `demo@northside.dental` / `demo1234`
- Agency: `owner@starling.app` / `demo1234`

```bash
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8790
```

Live: https://starling-production-5190.up.railway.app

## Review Insights (free, local)

- Dashboard **Home → "What customers talk about"**: themes across every review (staff, wait time, price & billing,
  quality, cleanliness, communication, booking, location), a **Fix first** / **Keep doing** call-out,
  hidden complaints (4–5★ ratings with unhappy words), top phrases and a 30-day mood trend. `GET /api/insights`.
- Public lead magnet: **/review-analyzer** — paste reviews, get the same report. `POST /api/public/insights`.
- Runs in-process with no AI credits: sentence sentiment by [VADER](https://github.com/cjhutto/vaderSentiment)
  (MIT) plus Starling's own aspect lexicon and complaint patterns (`app/insights.py`).

## Instant Local Reply Pack (free, local)

- Dashboard **Home → Needs a reply → Instant reply**: brand-safe public reply drafted in-process from
  rating + review text + industry. **No AI credits**, no network. `POST /api/reviews/{id}/send-local`
  (also `/local-reply` for draft-only). Grok AI reply still available; if Grok fails, Starling falls
  back to this pack automatically.
- Public lead magnet on **/review-analyzer**: **Draft reply** uses `POST /api/public/local-reply`.
- Health flag: `local_reply: instant-pack-v1`.
- Original Starling templates in `app/local_replies.py`. Aspect cues reuse Review Insights
  (VADER MIT). Idea inspiration for a free local path without an LLM key:
  [haqaliz/rereflect](https://github.com/haqaliz/rereflect) (MIT) — no code copied.

## NAP Diff Pack (`nap-diff-v1`)

Backend-only Name/Address/Phone consistency grading for listings sync and API clients.

- `POST /api/public/nap-diff` — free grade (nothing stored)
- `POST /api/listings/nap-diff` — grade an observed listing vs the signed-in location
- `POST /api/listings/sync` — OpenStreetMap / Google rows now store a JSON `detail` grade (`match` / `formatting` / `incomplete` / `mismatch`); `nap_match` stays 1 for match+formatting
- Health: `nap_diff: "nap-diff-v1"`

Idea inspiration: [Citewatch / local-seo-citation-tracker-mvp](https://github.com/bensblueprints/local-seo-citation-tracker-mvp) (MIT) — original Starling Python, no vendored UI or Node source.

## Ask Cooldown Pack (`ask-cooldown-v1`)

Backend-only Review Request Guard. Holds auto SMS/email when the same customer
already got a successful ask inside `ASK_COOLDOWN_HOURS` (default 72). Link
channel unchanged. Soft kill-switch: `ASK_COOLDOWN=0`.

- Health: `ask_cooldown: "ask-cooldown-v1"`
- Ops: `GET /api/ask-cooldown/summary`

Idea inspiration: SafeAgent exactly-once receipts (Show HN) + privacy-timeguard
(MIT) time windows — original Starling Python, no vendored code.


## Widget Guard Pack (`widget-guard-v1`)

Backend-only spend brake for the public web-chat widget (`POST /api/widget/chat`, no login), which calls the paid
Grok API for each message.

- Paid AI replies capped per visitor (8 per 10 min; salted hash of the client IP, never stored raw), per location
  (60/hour) and globally (300/hour). Only the first 2000 characters of a message go to Grok.
- Over the cap the visitor gets the same fallback reply the widget already used when Grok was unavailable; the message
  is still saved to the inbox and is not metered as AI. Response shape unchanged.
- `GET /api/widget-guard/summary` (counts only), health `widget_guard: "widget-guard-v1"`.
- Env: `WIDGET_GUARD=0` kill switch; `WIDGET_GUARD_CLIENT_AI_PER_WINDOW`, `WIDGET_GUARD_WINDOW_SEC`,
  `WIDGET_GUARD_LOCATION_AI_PER_HOUR`, `WIDGET_GUARD_GLOBAL_AI_PER_HOUR`, `WIDGET_GUARD_PROMPT_CHARS`.

Idea inspiration (no code copied): [laurentS/slowapi](https://github.com/laurentS/slowapi) (MIT),
[animir/node-rate-limiter-flexible](https://github.com/animir/node-rate-limiter-flexible) (ISC),
[express-rate-limit](https://github.com/express-rate-limit/express-rate-limit) (MIT).

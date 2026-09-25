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

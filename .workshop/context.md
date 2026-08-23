# Rental Fraud Detector ("fillory fraud detector") — Project Context

## Architecture
- **Frontend**: React + TypeScript (Vite), shadcn/ui, Tailwind CSS, recharts, lucide-react
- **Backend**: FastAPI (Python), entry `app.py` (`asgi = create_app("./dist")`)
- **Database**: Neon PostgreSQL (prefix DBFB9343D8)
- **AI**: Gemini via Workshop proxy (`gemini-3.5-flash` primary, `gemini-3.1-flash-lite` fallback — old names `gemini-2.5-flash-preview-04-17`/`gemini-2.0-flash` are DEAD on the proxy)
- **Scraping**: Apify actors (account `dwntwnmattbrown`) + self-hosted Craigslist detail fetching
- **SMS**: Twilio — **SAFE MODE**: `TWILIO_ENABLED` env (default false) gates all Twilio calls in `notifier.py`. Alerts recorded with status "skipped". Flip only after publishing to final URL.
- **NO GIT REPO YET** — user has not initialized one; recommended but not done.

## Local Dev Machine Notes (macOS)
- `uv` + `bun` at `~/.local/bin` / `~/.bun/bin` (start.sh exports PATH)
- Secrets in macOS keychain service **"workshop"**; `config.py:_env_or_keyring()` falls back env → keyring("workshop") → keyring("memex")
- Backend runs on APP_PORT+100 (3176 when Vite is 3076); uvicorn --reload watches project .py files
- Editing .py files mid-scan triggers reload and can abort a running scan

## Key Files
- `routes.py` — API routes + `run_scan` pipeline + `_process_listing` (shared by scan and import)
- `detector.py` — 3-signal matching + Gemini analysis + `parse_listing_text` (paste import)
- `scraper.py` — Apify actors + `_build_listing` normalization
- `craigslist_detail.py` — **NEW** detail-page enricher (body/address/geo)
- `geocode.py` — **NEW** Nominatim geocoding + `haversine_metres` + `GEO_MATCH_RADIUS_METRES=150`
- `notifier.py` / `config.py` / `app.py`
- `docs/layer3-image-hashing-spec.md` — pHash spec for the property firm (not built)

## MEASURED source data facts (2026-08-22, don't re-litigate)
- Craigslist search rows: **0% descriptions**, 14% street address in `location` → REQUIRES detail fetch
- Craigslist detail pages: plain httpx GET, browser UA, 1.5s delay → **12/12 then 50/50 HTTP 200, no blocks, no proxy needed**. Serve `#postingbody`, `<div class="mapaddress">`, `data-latitude/longitude`
- After enrichment: **100% descriptions, 98% street addresses, 98% coordinates**
- Facebook: descriptions are **FULL** in DB (mean 1074, max 4746 chars). The old "303 char truncation" was OUR `to_dict()` clipping at 300 — NOT a scraper limit
- Facebook **never** exposes a street address for rentals (structural, city+ZIP only) → no geo match possible for FB; image hashing is the only real hook

## Matching Logic (`_find_best_address_match`, threshold 0.5)
Three signals, strongest first:
1. **Geo** — listing/property coords within 150m → score 1.0
2. **Address field** — `street_address` (enriched) or `location`, via `_address_match_score` with generic + per-property city/state stopwords
3. **Address in title/body** — `_text_contains_address` requires street number as standalone token AND a distinctive street-name token (high precision, avoids "4411 sqft" / "Killingsworth" alone)

Verified 10/10 on: geo exact, geo 80m, geo 900m (reject), street_address field, addr in body only, addr in title only, same-street-diff-number (reject), same-number-diff-street (reject), city-only (reject), stray-number (reject).

No match → "unknown" (NOT fraud). Gemini failure → "unknown". Fraud = impersonating a REGISTERED property (match + anomaly).

## Gemini prompt
`_FEW_SHOT_EXAMPLES` in detector.py holds a calibration pair (Killingsworth scam @1.0 / legit @0.95) + rule "address match alone is NOT fraud". Regression-tested: scam→fraud 1.0, legit→legitimate 0.95, unrelated→unknown 0.0.

## Geocoding gotcha
Nominatim returns nothing for "4411 NE Killingsworth St **Unit 107**, Portland, OR". `geocode.py:_strip_unit()` removes unit/apt/ste/# designators and retries. Both properties now geocoded.

## Schema notes
`database.py:_run_migrations()` runs additive idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on init_db(). Added: properties.latitude/longitude/geocoded_at, scraped_listings.street_address/latitude/longitude/enriched.
`ScrapedListing.to_dict(full=False)` truncates description to 300; `GET /api/listings/{id}` passes `full=True`. Detail dialog fetches the single-listing endpoint for untruncated text.

## UI
Tabs: Dashboard / Import / Properties / Listings / Scans / Alerts. Logo at `public/fillory-logo.png` (pink circle "f"), used in TopNav + favicon.
`ListingDetailDialog.tsx` — click any listing row or dashboard flagged card. Shows verdict, confidence, full reason, matched property, street address, map link, full body, images.
Manual-source listings render as **"pasted by you"** (amber) to avoid mistaking test data for live hits.
Delete button per listing row → `DELETE /api/listings/{id}` (also clears child Alerts).

## Known Limitations / TODO
- **Layer 3 (image pHash) not built** — spec in docs/. Needs property firm's marketing photos. Only viable fraud signal for Facebook.
- Facebook detail enrichment would need proxies/session cookies (not attempted)
- Scan re-inserts duplicate listings across scans (no external_id dedup)
- No scheduled scans
- Not published; Twilio must stay disabled until published + final URL verified

## Registered Properties
- "3BR Townhome — 4411 NE Killingsworth St Unit 107", Portland OR 97218, 3bd/1.5ba, $2995 — geo 45.5628691,-122.6180357 (user's real unit)
- "123 Main St Rental", Austin TX (test data) — geo 30.3026459,-97.7619053

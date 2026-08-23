# Rental Fraud Detector ("fillory fraud detector") — Project Context

## Architecture
- **Frontend**: React + TypeScript (Vite), shadcn/ui, Tailwind CSS 3.4.1, recharts, lucide-react
- **Backend**: FastAPI (Python), entry `app.py` (`asgi = create_app("./dist")`)
- **Database**: Neon PostgreSQL (prefix DBFB9343D8)
- **AI**: Gemini via Workshop proxy (`gemini-3.5-flash` primary, `gemini-3.1-flash-lite` fallback — old names `gemini-2.5-flash-preview-04-17`/`gemini-2.0-flash` are DEAD on the proxy)
- **Scraping**: Apify actors (account `dwntwnmattbrown`) + self-hosted Craigslist detail fetching
- **SMS**: Twilio — **SAFE MODE**: `TWILIO_ENABLED` env (default false) gates all Twilio calls in `notifier.py`. Alerts recorded with status "skipped". Flip only after publishing to final URL.

## Git / GitHub
- Repo: **https://github.com/fillory-ai/Fillory-Fraud**, branch `main`, remote `origin` (plain HTTPS URL, no token embedded)
- Connector "Fillory Fraud" (prefix **FILLOR**) → secret `FILLOR_GITHUB_TOKEN`, a **classic** PAT with `repo` scope on account `fillory-ai`
- **macOS `osxkeychain` credential helper breaks pushes** (feeds stale creds before askpass). Fixed repo-locally:
  - `core.askPass` → `.git/gh-askpass.sh` (reads token from keychain service `workshop`, username `fillory-ai`)
  - `credential.helper` → `""`
  - `.git/` is untracked so the script can't leak. Plain `git push` now works.
- When the PAT expires, pushes fail with "Invalid username or token" → regenerate + update connector in Hub.

## Local Dev Machine Notes (macOS)
- `uv` + `bun` at `~/.local/bin` / `~/.bun/bin` (start.sh exports PATH)
- Secrets in macOS keychain service **"workshop"**; `config.py:_env_or_keyring()` falls back env → keyring("workshop") → keyring("memex")
- Backend runs on APP_PORT+100 (3176 when Vite is 3076); uvicorn --reload watches project .py files
- Editing .py files mid-scan triggers reload and can abort a running scan

## Key Files
- `routes.py` — API routes + `run_scan` pipeline + `_process_listing` (shared by scan and import)
- `detector.py` — 4-signal matching + Gemini analysis + `parse_listing_text` (paste import)
- `scraper.py` — Apify actors + `_build_listing` normalization
- `craigslist_detail.py` — detail-page enricher (body/address/geo)
- `geocode.py` — Nominatim geocoding + `haversine_metres` + `GEO_MATCH_RADIUS_METRES=150`
- `test_matcher.py` — **17-case matcher regression suite**, run `uv run python test_matcher.py`
- `notifier.py` / `config.py` / `app.py`
- `docs/layer3-image-hashing-spec.md` — pHash spec for the property firm (not built)

## MEASURED source data facts (don't re-litigate)
### Craigslist
- Search rows: **0% descriptions**, 14% street address in `location` → REQUIRES detail fetch
- Detail pages: plain httpx GET, browser UA, 1.5s delay → **50/50 HTTP 200, no blocks, no proxy needed**. Serve `#postingbody`, `<div class="mapaddress">`, `data-latitude/longitude`
- After enrichment: **100% descriptions, 98% street addresses, 98% coordinates**
- (Web research claiming "residential proxies mandatory" was WRONG — measurement beat chatter.)

### Facebook (measured on 90 stored rows, 2026-08-22)
- description **100%** (mean 1074, max 4746 chars — FULL. The old "303 char" story was OUR `to_dict()` clipping at 300)
- image_urls **100%** (mean 8.8/listing, 64/90 have all 10)
- url + external_id **100%**
- location **91%**, of which **80% carry ZIP+4** (`Portland, OR, 97232-1244`)
- street_address field **0%** — structural, FB never exposes one for rentals
- street address inside the body: **13%** (12/90) → already caught by `_text_contains_address`
- **FB location is truthful, not fuzzed**: geocoded the 12 body addresses, compared ZIP to FB's reported ZIP → **11 agree, 0 disagree, 1 ungeocodable**

## Matching Logic (`_find_best_address_match`, threshold 0.5)
Four signals, strongest first:
1. **Geo** (1.0) — listing/property coords within 150m
2. **Address in title/body** (0.9) — `_text_contains_address`; requires street number as standalone token AND a distinctive street-name token
3. **Address field** (0..1) — `street_address` (enriched) or `location`, via `_address_match_score`
4. **ZIP+4** (0.7) — exact 9-digit agreement between listing location/street_address and `Property.zip_plus4`. Only geo hook available on Facebook.

**A shared 5-digit ZIP is deliberately NOT a match.** Two guards in `_address_match_score`:
- The substring shortcut now requires the street numbers to agree. Previously `"portland or 97218"` was a literal substring of `"4411 ne killingsworth st unit 107 portland or 97218"` → scored 1.0 and matched a $600 roommate-wanted post to the townhome.
- Token overlap consisting solely of 5-digit ZIPs is capped at 0.3.

No match → "unknown" (NOT fraud). Gemini failure → "unknown". Fraud = impersonating a REGISTERED property (match + anomaly).

`test_matcher.py` covers all 17 cases (geo exact/80m/900m-reject, street_address field, addr in body/title, same-street-diff-number, same-number-diff-street, city-only, stray-number, bare-ZIP rejects ×2, ZIP+4 exact/wrong/in-street_address/no-leak/loses-to-geo). All passing.

## Gemini prompt
`_FEW_SHOT_EXAMPLES` in detector.py holds a calibration pair (Killingsworth scam @1.0 / legit @0.95) + rule "address match alone is NOT fraud". Regression-tested: scam→fraud 1.0, legit→legitimate 0.95, unrelated→unknown 0.0.

## Geocoding gotcha
Nominatim returns nothing for "4411 NE Killingsworth St **Unit 107**, Portland, OR". `geocode.py:_strip_unit()` removes unit/apt/ste/# designators and retries.

## Schema notes
`database.py:_run_migrations()` runs additive idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on init_db(). Added: properties.latitude/longitude/geocoded_at/**zip_plus4**, scraped_listings.street_address/latitude/longitude/enriched.
`ScrapedListing.to_dict(full=False)` truncates description to 300; `GET /api/listings/{id}` passes `full=True`.

## UI
Tabs: Dashboard / Import / Properties / Listings / Scans / Alerts. Logo at `public/fillory-logo.png`, used in TopNav + favicon.
`ListingDetailDialog.tsx` — click any listing row or dashboard flagged card; fetches the single-listing endpoint for untruncated text.
Manual-source listings render as **"pasted by you"** (amber).
Delete button per listing row → `DELETE /api/listings/{id}` (also clears child Alerts).
Property form has a **ZIP+4** field (validated `^$|^\d{5}-\d{4}$`).

## Known Limitations / TODO
- **ZIP+4 not yet populated for either property** — user must look up at https://tools.usps.com/zip-code-lookup.htm. Until then signal 4 is inert.
- **Layer 3 (image pHash) not built** — spec in docs/. Steps 1–5 buildable now against Killingsworth photos; 6–7 need the firm's marketing photos. Only signal that PROVES impersonation on Facebook.
- Facebook detail enrichment would need proxies/session cookies (not attempted)
- Scan re-inserts duplicate listings across scans (no external_id dedup) — craigslist is at 100 rows from 2×50 scans
- No scheduled scans
- Not published; Twilio must stay disabled until published + final URL verified

## Registered Properties
- "3BR Townhome — 4411 NE Killingsworth St Unit 107", Portland OR 97218, 3bd/1.5ba, $2995 — geo 45.5628691,-122.6180357 (user's real unit). zip_plus4 **not set**.
- "123 Main St Rental", Austin TX 78701 (test data) — geo 30.3026459,-97.7619053. zip_plus4 **not set**.

## Data hygiene
One `manual` test row remains (the *legitimate* pasted Killingsworth listing, used as a positive control — it still matches correctly).

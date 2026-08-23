# Rental Fraud Detector ("fillory fraud detector") — Project Context

## Architecture
- **Frontend**: React + TypeScript (Vite), shadcn/ui, Tailwind CSS 3.4.1, recharts, lucide-react
- **Backend**: FastAPI (Python), entry `app.py` (`asgi = create_app("./dist")`)
- **Database**: Neon PostgreSQL (prefix DBFB9343D8), schema managed by **Alembic**
- **AI**: Gemini via Workshop proxy (`gemini-3.5-flash` primary, `gemini-3.1-flash-lite` fallback — old names `gemini-2.5-flash-preview-04-17`/`gemini-2.0-flash` are DEAD on the proxy)
- **Scraping**: Apify actors (account `dwntwnmattbrown`) + self-hosted Craigslist detail fetching
- **SMS**: Twilio — **SAFE MODE**: `TWILIO_ENABLED` env (default false) gates all Twilio calls in `notifier.py`. Additionally `OBSERVE_MODE` (default **true**) means nothing is ever sent. Flip only after publishing to final URL.

## Git / GitHub
- Repo: **https://github.com/fillory-ai/Fillory-Fraud**, branch `main`, remote `origin` (plain HTTPS URL, no token embedded)
- Connector "Fillory Fraud" (prefix **FILLOR**) → secret `FILLOR_GITHUB_TOKEN`, a **classic** PAT with `repo` scope on account `fillory-ai`
- **macOS `osxkeychain` credential helper breaks pushes**. Fixed repo-locally: `core.askPass` → `.git/gh-askpass.sh`, `credential.helper` → `""`. Plain `git push` works.
- **Verify pushes with `git log origin/main..main`.** Claimed-pushed-but-local-only has happened once.

## Local Dev Machine Notes (macOS)
- `uv` + `bun` at `~/.local/bin` / `~/.bun/bin` (start.sh exports PATH)
- Secrets in macOS keychain service **"workshop"**; `config.py:_env_or_keyring()` falls back env → keyring("workshop") → keyring("memex")
- Backend runs on APP_PORT+100 (3176 when Vite is 3076); uvicorn --reload watches project .py files
- Editing .py files mid-scan triggers reload and can abort a running scan

## Key Files
- `pipeline.py` — **M1**: scan pipeline (`run_scan`, `process_listing`, `_upsert_listing`/`_claim_listing`, `_open_or_update_case`, `_record_alert`, `_mark_delisted`, `_recent_source_average`). Extracted from routes.py so the scheduler needn't import the web layer.
- `scheduler.py` — APScheduler; scan job gated by `SCHEDULER_ENABLED` (default false), hourly health check always on. `scan_health()` is **global freshness only**.
- `routes.py` — API routes only; `_case_payload()` is shared by GET and PUT `/api/cases`
- `detector.py` — 4-signal matching (`_match_with_signal`) + Gemini analysis + `parse_listing_text`
- `scraper.py`, `craigslist_detail.py`, `geocode.py` (150m radius), `notifier.py`, `config.py`, `database.py`, `models.py`
- `migrations/` — Alembic. `env.py` has an `include_object` filter that **must** stay: without it autogenerate proposes DROPPING the Neon Auth tables visible via search_path.
- Tests: `test_matcher.py` (17), `test_pipeline.py` (**45**), `test_migration_collapse.py` (19), `test_case_api.py` (8, needs dev server up)
- `docs/system-spec.md`, `docs/property-firm-brief.md`, `docs/v1-spec.md`, `docs/layer3-image-hashing-spec.md`, `docs/connector-resilience-spec.md`

## Migrations (M0)
`init_db()` runs `alembic upgrade head` — no more `create_all()` + hand-written ALTERs.
Revisions: `372243be407d` baseline → `7980ad450dea` listing identity/cases/scan metrics → `d01496d3604b` delist guard.
Gotchas learned: NOT NULL adds on populated tables need `server_default`; `DROP TABLE` does not drop its enum type (downgrade must drop it explicitly); PostgreSQL has no `MIN()` for uuid (use `(array_agg(x))[1]`).

## M1 semantics (do not re-litigate)
- **Listing identity** = `(source, external_id)`, partial unique index (NULL external_id = manual paste, always inserted). Insert is `ON CONFLICT DO NOTHING` restating the partial predicate so Postgres can infer the arbiter.
- **content_fingerprint** hashes title/price/description/location/street_address/image_urls. Unchanged on re-sighting → skip the AI call. This is the cost gate on frequent scanning.
- **Case** = one (listing, property) pair; the unit of *alerting*. Opened once, then silent unless content changes AND the cooldown elapsed. dismissed/resolved/disputed are permanently silent.
- **Alert policy order**: rate cap → observe mode → send. `observed` counts against the daily budget (so observe-mode volume = live volume) and starts the cooldown. `suppressed_rate_limit` starts the cooldown but doesn't consume budget. A **failed** send deliberately does NOT start the cooldown, so the next change retries. Every decision writes an Alert row.
- **Delisting** needs two guards: coverage (this scan's row count ≥ `DELIST_MIN_COVERAGE`×recent average from `scan_logs.source_counts`) and persistence (`consecutive_misses` ≥ `DELIST_MISS_THRESHOLD`, default 2). No scan history → no delisting. Re-sighting resets both.
- Case + alert are one transaction (`_open_or_update_case` flushes, `_record_alert` commits). Notifier exceptions are caught and recorded, never propagated.
- Config flags: `OBSERVE_MODE`(T) `ALERT_COOLDOWN_HOURS`(24) `MAX_ALERTS_PER_DAY`(10) `SCHEDULER_ENABLED`(F) `SCAN_INTERVAL_HOURS`(4) `SCAN_STALE_HOURS`(12) `DELIST_MIN_COVERAGE`(0.6) `DELIST_MISS_THRESHOLD`(2) `DELIST_COVERAGE_WINDOW`(5)

## M1.5 connector resilience — SPECIFIED, NOT BUILT
`docs/connector-resilience-spec.md`. Strategic position adopted: **the recurring fee is justified by connector upkeep, not the detection engine.** Platform breakage is a permanent operating condition; silent failure is the enemy because "0 fraud found" reads as good news.

**KNOWN LIVE BUG (spec phase a0, highest value):** `scraper.py:169-172` and `211-214` do `except Exception: return []`. Apify outages, 403s, timeouts all reach `run_scan()` as *succeeded, zero rows*. `source_ok[src] = True` regardless of row count, and `source_ok` is in-memory only (never persisted). The per-source `try/except` in `pipeline.py:570-596` is very nearly dead code. Fix = `SourceResult(rows, ok, error)` NamedTuple; do NOT let exceptions propagate.

Other confirmed gaps: `scan_health()` has no per-source dimension and ages from `started_at` not `completed_at` (`scheduler.py:65`); `ScanLog.to_dict()` omits `source_counts`; no field-completeness floors; Apify actor IDs unpinned.

Design decisions already made: four states `ok`/`degraded`/`down`/`unknown` (`unknown` must not collapse into `ok`); extend `source_counts` JSON *values* from int to object with a tolerant `_row_count()` reader rather than adding a column (**no migration**); `SOURCE_DEGRADED_COVERAGE` 0.5 deliberately looser than `DELIST_MIN_COVERAGE` 0.6 because delisting is destructive and reporting isn't; coverage widget always visible, never conditional; operational alerts stay OUT of the fraud-alert SMS budget.

Craigslist and Facebook are **not equally fragile** — CL detail fetch is self-hosted and same-day fixable, FB is a single third-party actor on a hostile platform. Restore-time language should differ.

## MEASURED source data facts (don't re-litigate)
### Craigslist
- Search rows: **0% descriptions**, 14% street address in `location` → REQUIRES detail fetch
- Detail pages: plain httpx GET, browser UA, 1.5s delay → **50/50 HTTP 200, no blocks, no proxy needed**
- After enrichment: **100% descriptions, 98% street addresses, 98% coordinates**
### Facebook (measured on 90 stored rows, 2026-08-22)
- description **100%** (mean 1074, max 4746 chars); image_urls **100%** (mean 8.8); url + external_id **100%**
- location **91%**, of which **80% carry ZIP+4**; street_address field **0%** (structural); street address in body **13%**
- **FB location is truthful, not fuzzed** (11 of 12 geocoded body addresses agree on ZIP, 0 disagree)
### Duplicates
- Measured 2026-08-22: **191 rows, 0 duplicates**. The v1 spec's "50 duplicate craigslist rows" claim was WRONG.

## Normalized listing dict
`scraper._build_listing()` emits exactly: source, external_id, title, price, location, description, url, image_urls, posted_date. `craigslist_detail.py` enrichment adds: street_address, **latitude**, **longitude**, enriched. (Note: `latitude`/`longitude`, not `coordinates`.)

## Matching Logic (`_find_best_address_match` → `_match_with_signal`, threshold 0.5)
1. **Geo** (1.0) — within 150m
2. **Address in title/body** (0.9) — requires street number as standalone token AND a distinctive street-name token
3. **Address field** (0..1) — `street_address` or `location`
4. **ZIP+4** (0.7) — exact 9-digit agreement. Only geo hook available on Facebook.

**A shared 5-digit ZIP is deliberately NOT a match.** No match → "unknown" (NOT fraud). Gemini failure → "unknown". Fraud = impersonating a REGISTERED property (match + anomaly).

## Gemini prompt
`_FEW_SHOT_EXAMPLES` in detector.py holds a calibration pair (Killingsworth scam @1.0 / legit @0.95) + rule "address match alone is NOT fraud".

## Geocoding gotcha
Nominatim returns nothing for "... **Unit 107**, Portland, OR". `geocode.py:_strip_unit()` removes unit/apt/ste/# and retries.

## UI
Tabs: Dashboard / Import / Properties / Listings / **Cases** / Scans / Alerts. Logo at `public/fillory-logo.png`.
`CasesTab.tsx` — review queue with status filter and Acknowledge/Resolve/Dismiss.
`ListingDetailDialog.tsx` — click any listing row or dashboard flagged card.
`DashboardTab.tsx:17` — `const showHealthBanner = health && !health.healthy;` (truthiness, not `=== false`).

## Known Limitations / TODO
- **M1.5 phase a0 scraper bug** — see above; makes most failures invisible
- **ZIP+4 not yet populated for either property** — signal 4 is inert until it is
- **Layer 3 (image pHash) not built** — spec in docs/. Only signal that PROVES impersonation on Facebook
- **The firm's own marketing listings will match their own properties on every signal** — authorized-poster allowlist + property lifecycle specified in v1-spec §6 but NOT built
- Facebook detail enrichment would need proxies/session cookies (not attempted)
- Recall unmeasured (M2, needs the firm's historical scams)
- M0 remainder: CI, staging Neon branch, error tracking
- Not published; Twilio + OBSERVE_MODE must stay off until published + final URL verified

## Registered Properties
- "3BR Townhome — 4411 NE Killingsworth St Unit 107", Portland OR 97218, 3bd/1.5ba, $2995 — geo 45.5628691,-122.6180357. zip_plus4 **not set**.
- "123 Main St Rental", Austin TX 78701 (test data) — geo 30.3026459,-97.7619053. zip_plus4 **not set**.

## Data hygiene
One `manual` test row remains (the *legitimate* pasted Killingsworth listing, a positive control). Test suites use `test-m1-` / `test-api-` prefixes and clean up after themselves.

## Working practice that has paid off
Write the spec, then **fact-check its claims about current code with a verification subagent before committing.** Doing this on the M1.5 spec caught three wrong assertions and surfaced the a0 scraper bug, which nobody had noticed in M1's review.
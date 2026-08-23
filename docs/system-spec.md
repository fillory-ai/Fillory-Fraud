# fillory fraud detector — System Specification

**Status:** built and running locally; not yet published
**Audience:** Matt (internal / engineering reference)
**Date:** 2026-08-22
**Repo:** https://github.com/fillory-ai/Fillory-Fraud

---

## 1. What the product does

A rental management firm publishes listings for the units it manages.
Scammers copy those listings — the address, the photos, the description — and
repost them on Craigslist and Facebook Marketplace at a below-market price to
harvest deposits from people who will never get a key.

This system continuously scans those marketplaces, decides whether a given
listing is impersonating a property the firm actually manages, and raises an
alert when it is.

### The core definition, which everything else follows from

> **Fraud = a listing that impersonates a REGISTERED property AND shows
> anomalies consistent with a scam.**

Both halves are required. This is the single most important design decision in
the system, and it shapes every threshold below.

- A listing that matches a registered property but looks legitimate (the
  firm's own ad, a tenant's sublet post) is **legitimate**, not fraud.
- A listing that looks scammy but matches no registered property is
  **unknown**, not fraud. The internet is full of rental scams; they are not
  our problem unless they target our client's properties.
- A listing we cannot confidently place is **unknown**, never fraud.

The system is deliberately biased toward silence. A false alarm sent to a
property manager costs credibility, and credibility is the whole product.

---

## 2. Architecture

```
                 ┌──────────────────────────────────────────┐
                 │  React + TypeScript (Vite)               │
                 │  Dashboard / Import / Properties /       │
                 │  Listings / Scans / Alerts               │
                 └───────────────┬──────────────────────────┘
                                 │ /api/*
                 ┌───────────────▼──────────────────────────┐
                 │  FastAPI  (app.py → routes.py)           │
                 └───────────────┬──────────────────────────┘
                                 │
      ┌──────────────┬───────────┼────────────┬──────────────┐
      │              │           │            │              │
┌─────▼─────┐  ┌─────▼──────┐ ┌──▼───────┐ ┌──▼─────────┐ ┌──▼────────┐
│ scraper   │  │ craigslist │ │ geocode  │ │ detector   │ │ notifier  │
│ (Apify)   │  │ _detail    │ │(Nominatim│ │ (matching  │ │ (Twilio,  │
│           │  │ (httpx)    │ │ +haversine│ │ + Gemini)  │ │  gated)   │
└───────────┘  └────────────┘ └──────────┘ └────────────┘ └───────────┘
                                 │
                 ┌───────────────▼──────────────────────────┐
                 │  Neon PostgreSQL                         │
                 │  properties / scraped_listings /         │
                 │  alerts / scan_logs                      │
                 └──────────────────────────────────────────┘
```

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind 3.4.1, shadcn/ui, recharts | |
| Backend | FastAPI, SQLAlchemy, Pydantic | `app.py` exposes `asgi = create_app("./dist")` |
| Database | Neon PostgreSQL | connector prefix `DBFB9343D8` |
| AI | Gemini via Workshop proxy | `gemini-3.5-flash`, falls back to `gemini-3.1-flash-lite` |
| Scraping | Apify actors + self-hosted httpx fetching | |
| SMS | Twilio | **disabled by default** — see §8 |

**Local dev:** Vite on `APP_PORT` (3076), FastAPI on `APP_PORT+100` (3176).
Secrets resolve env → macOS keychain service `workshop` → keychain `memex`,
via `config.py:_env_or_keyring()`.

---

## 3. The scan pipeline

`routes.py:run_scan()` — triggered by `POST /api/scan?source=all|craigslist|facebook_marketplace`.

```
1. Scrape         Apify actors return search-result rows
                    craigslist:  automation-lab/craigslist-scraper
                    facebook:    apify/facebook-marketplace-scraper

2. Enrich         Craigslist only. Fetch each posting page directly with
                  httpx to recover body text, street address, coordinates.
                  (Facebook has no equivalent — see §5.)

3. Geocode        Any property lacking coordinates is geocoded once via
                  Nominatim and cached on the row. No-op after first scan.

4. Match          detector._find_best_address_match()
                  Is this listing about one of OUR properties? Four signals.
                  No match → verdict "unknown", pipeline stops here.
                  This is the cost gate: no match means no AI call.

5. Analyze        detector.analyze_listing()
                  Only for matched listings. Gemini compares the listing
                  against the real property record and returns
                  {fraud_status, confidence, reason}.

6. Alert          notifier. Fraud verdict → Alert row + SMS.
                  SMS is gated by TWILIO_ENABLED (default false); alerts are
                  still recorded, with status "skipped".
```

Every stage is wrapped in its own `try/except`. A Craigslist block, a
Nominatim outage, or a Gemini failure degrades the scan rather than killing
it. Enrichment failure in particular is non-fatal — we fall back to
unenriched rows.

---

## 4. Detection: the four matching signals

`detector.py:_find_best_address_match()`. Threshold **0.5**. Strongest signal
wins per property; the best-scoring property across the portfolio is returned.

| # | Signal | Score | Where it works |
|---|---|---|---|
| 1 | **Geo proximity** — listing coords within 150 m of property coords | 1.0 | Craigslist (98% coverage after enrichment) |
| 2 | **Address in title/body** — street number + distinctive street name both present as tokens | 0.9 | Both (Craigslist 100%, Facebook 13%) |
| 3 | **Address field** — fuzzy score against `street_address` or `location` | 0–1.0 | Craigslist |
| 4 | **ZIP+4** — exact 9-digit agreement | 0.7 | Facebook (80% coverage) — the only geo hook there |

### Why 150 m

Tight enough to exclude the next block, loose enough to absorb geocoder
disagreement between Nominatim's rooftop estimate and Craigslist's own pin.
Verified: an 80 m offset matches, a 900 m offset does not.

### Why a shared 5-digit ZIP is NOT a match

This was a real bug, not a hypothetical. `_address_match_score` had a
substring shortcut: if the normalized listing location appeared inside the
normalized property address, return 1.0. But:

```
listing  "portland or 97218"
property "4411 ne killingsworth st unit 107 portland or 97218"
                                            └── literal substring ──┘
```

Every Facebook listing in ZIP 97218 scored a perfect match. In practice this
matched a **$600 roommate-wanted post** to a $2,995 registered townhome. Only
Gemini caught it, and meanwhile every listing in the property's ZIP was
burning an API call.

Two guards now:

1. The substring shortcut requires the **street numbers to agree**.
2. Token overlap consisting **solely of 5-digit ZIPs** is capped at 0.3 —
   below the 0.5 threshold, so it can narrow candidates but never declare a
   match.

A 5-digit ZIP is neighbourhood-level evidence. A ZIP+4 is roughly one block
face or one building. The first is worthless for identity; the second is
worth 0.7.

### Guards against false positives

- City and state tokens are treated as stopwords **per property**, so
  "Portland" never contributes to a match against a Portland property.
- Street types and directions (`st`, `ave`, `ne`, `unit`…) are stopwords.
- A street-number match only counts if a distinctive non-numeric token is
  *also* shared — so `4411 NE Killingsworth` matches but `4411 NE Broadway`
  does not.
- `_text_contains_address` requires the street number as a **standalone
  token**, so "4,411 sqft" does not trigger a match on 4411.

### Regression suite

`test_matcher.py` — 17 cases, all passing. Run with
`uv run python test_matcher.py`.

Covers: geo exact / 80 m / 900 m-reject; street_address field; address in body;
address in title; same-street-different-number; same-number-different-street;
city-only; stray number; bare-ZIP rejects (×2); ZIP+4 exact / wrong / in
street_address / no-leak-to-other-property / loses-to-geo.

This suite is the contract. Any change to matching logic runs it first.

---

## 5. Source data — measured, not assumed

Every number below was measured against real stored rows. Several contradict
what the documentation and the wider internet claim, which is why they are
recorded here.

### Craigslist

| | Search rows (Apify) | After our enrichment |
|---|---|---|
| Description | **0%** | **100%** |
| Street address | 14% | **98%** |
| Coordinates | 0% | **98%** |

The Apify actor returns search-result rows only — no posting body, rarely an
address. Without enrichment, Craigslist detection was effectively blind.

`craigslist_detail.py` fetches each posting page directly: plain `httpx` GET,
one browser User-Agent, 1.5 s delay between requests. Result: **50/50 HTTP
200, no blocks, no proxy, no session cookies.** Pages serve `#postingbody`,
`<div class="mapaddress">`, and `data-latitude` / `data-longitude`.

> **Note on research quality.** Web research strongly asserted that Craigslist
> scraping requires residential proxies and tolerates only ~5 requests/minute.
> Direct measurement disproved this for our access pattern. Measurement beat
> chatter; this is worth remembering before paying for proxy infrastructure.

Safeguards regardless: 1.5 s delay, backoff and abort on 403/429, cap of 60
detail fetches per scan, all failures non-fatal.

### Facebook Marketplace (measured on 90 stored rows)

| Field | Coverage |
|---|---|
| Description | **100%** — mean 1,074 chars, max 4,746 (full text) |
| Image URLs | **100%** — mean 8.8 per listing; 64/90 have all 10 |
| URL + external_id | **100%** |
| Location | 91% — and **80% of those carry ZIP+4** |
| Street address field | **0%** — structural; Facebook never exposes one for rentals |
| Street address inside body text | **13%** (12/90) |

Two corrections worth recording:

1. **Facebook descriptions were never truncated.** An earlier belief that the
   scraper capped bodies at ~303 characters was wrong — that was *our own*
   `ScrapedListing.to_dict()` clipping at 300 and appending `"..."`. Raw
   database values average 1,074 characters. `to_dict(full=True)` returns the
   whole thing, and `GET /api/listings/{id}` uses it.

2. **Facebook's location is truthful, not fuzzed.** The concern was that
   Facebook deliberately blurs rental locations, which would poison ZIP+4
   matching. Tested it: took the 12 listings with a real street address in the
   body, geocoded that address, compared the resulting ZIP against Facebook's
   reported ZIP. **11 agree, 0 disagree, 1 ungeocodable.** ZIP+4 is safe to
   rely on.

---

## 6. AI analysis

`detector.analyze_listing()` runs **only on matched listings**. Gemini receives
the listing and the real property record, and returns structured output:
`{fraud_status, confidence, reason}`.

### Prompt calibration

`_FEW_SHOT_EXAMPLES` carries a deliberate pair drawn from real data:

- **Example A** — a scam impersonating the Killingsworth townhome → `fraud`, ~1.0
- **Example B** — the genuine listing for the same unit → `legitimate`, ~0.95

Plus the explicit rule: **an address match alone is NOT fraud.** Without this,
the model treats "this listing is about a property we protect" as evidence of
guilt, which inverts the product.

Regression-verified: scam → fraud 1.0, legitimate → legitimate 0.95, unrelated
→ unknown 0.0.

### Failure behaviour

A Gemini error yields `unknown`, never `fraud`. We do not alert on a listing we
failed to analyze.

---

## 7. Data model

```
properties
  id, name, address, city, state, zip_code
  zip_plus4                     ← signal 4
  bedrooms, bathrooms, square_footage, monthly_rent
  description, image_urls, amenities
  latitude, longitude, geocoded_at   ← signal 1, cached

scraped_listings
  id, source, external_id, title, price, location, description, url
  image_urls, posted_date
  street_address, latitude, longitude, enriched   ← from detail enrichment
  fraud_status, fraud_confidence, fraud_reason
  matched_property_id, alerted_at

alerts        listing_id, message, status, error_message, sent_at
scan_logs     source, status, listings_found, started_at, completed_at
```

**Migrations.** SQLAlchemy's `create_all()` does not alter existing tables, so
`database.py:_run_migrations()` runs additive, idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements on every `init_db()`.
This is how `zip_plus4`, the geo columns and the enrichment columns landed on
a live database without a migration framework.

---

## 8. Twilio safe mode

`TWILIO_ENABLED` (env, default **false**) gates every Twilio call in
`notifier.py`. When disabled, alerts are still created and stored with
`status="skipped"` and an explanatory `error_message`.

The reason: the app is not published. A scan that fires real SMS to a real
phone number from a half-configured local instance is an unrecoverable
mistake. **Do not enable until the app is published and the final URL is
verified.**

---

## 9. Interface

Tabs: **Dashboard / Import / Properties / Listings / Scans / Alerts**

- **Dashboard** — counts, charts, and a flagged-listings card. Fraud rows are
  fetched separately (`fraud_status=fraud&limit=50`) rather than filtered from
  the shared recent-listings list, which is capped at 100 and could drop them.
- **Import** — paste raw listing text; `detector.parse_listing_text()`
  structures it and runs it through the same `_process_listing` path as a
  scan. Used for testing and for listings a user finds manually.
- **Listings** — click any row for `ListingDetailDialog`, which re-fetches the
  single-listing endpoint to get untruncated body text. Per-row delete
  (`DELETE /api/listings/{id}`, which also clears child alerts).
- **Properties** — CRUD, including the ZIP+4 field (validated `^$|^\d{5}-\d{4}$`).

Manual-source listings render as **"pasted by you"** in amber, so a test row
is never mistaken for a live marketplace find. This came directly from a real
confusion: a pasted test scam was reported as fraud and looked like a genuine
detection.

---

## 10. Known limitations

| Limitation | Impact | Fix |
|---|---|---|
| **ZIP+4 not yet populated** for either property | Signal 4 is inert | Look up + enter (see firm brief) |
| **Layer 3 (image pHash) not built** | Cannot prove impersonation on Facebook | Spec written; steps 1–5 buildable now |
| No `external_id` deduplication | Scans re-insert duplicates; Craigslist sits at 100 rows from 2×50 scans | Unique constraint + upsert |
| No scheduled scans | Scanning is manual | Cron / APScheduler |
| Facebook detail pages not fetched | No body enrichment beyond what search returns | Would need proxies + session cookies |
| Not published | Twilio must stay off | Publish, verify URL, then enable |
| Single-tenant | One firm, no auth | Fine for now |

---

## 11. Test and verification status

| Check | Result |
|---|---|
| `test_matcher.py` | 17/17 passing |
| Gemini calibration | scam→fraud 1.0, legit→legitimate 0.95, unrelated→unknown 0.0 |
| Craigslist live scan | 50 listings; 50/50 descriptions, 49/50 addresses, 49/50 coords |
| Craigslist detail fetch | 50/50 HTTP 200, no blocks |
| Facebook location truthfulness | 11/11 ZIP agreement vs geocoded body address |
| Full replay over 191 stored listings | Roommate false positive gone; genuine match retained; no new matches |
| TypeScript | `tsc --noEmit` clean |

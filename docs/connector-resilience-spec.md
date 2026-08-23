# Connector Resilience (M1.5)

**Status:** specified, not built
**Date:** 2026-08-23
**Depends on:** M1 (`scan_logs.source_counts`, `_recent_source_average`, scheduler health check)

---

## 0. Why this exists

Fillory reads two platforms we do not control and have no contract with. Craigslist
and Facebook Marketplace will change their markup, tighten anti-bot measures, and
occasionally break us outright. This is not a risk to be avoided; it is a permanent
operating condition.

The strategic consequence, which this spec commits us to:

> **The recurring fee is justified by connector upkeep, not by the detection engine.**

The engine — matching, Gemini analysis, cases, alerts — is a one-time build that
mostly stops changing. What a property firm pays for monthly is that someone is
watching the connectors and repairing them inside a stated window. If that is the
pitch, then per-source health is not a feature bolted onto the product. It *is* the
product's visible surface, and it has to be first-class in the UI and in the
contract.

A second consequence, and the reason this is urgent rather than tidy-up work:

> **In a monitoring product, silent failure is worse than loud failure.**

"0 fraud found" reads as good news. It is indistinguishable, to the customer, from
"the scraper has been returning an empty list for nine days." Every design decision
below follows from refusing to let those two states look alike.

---

## 1. What we already have

Worth being precise, because a good deal of the resilience story is already built and
this spec is mostly about *surfacing* data we are already writing.

| Concern | Status | Where |
|---|---|---|
| Per-source normalized contract | **Done** | `scraper.py`: `search_craigslist()` / `search_facebook_marketplace()` both return `list[dict]` via one normalizer, `_build_listing(source, item)` |
| Transport independence | **Done** | `craigslist_detail.py` is a second, self-hosted transport behind the same contract — nothing downstream knows the difference |
| Per-source failure isolation | **Done, but mostly unreachable** | `run_scan()` wraps each source in its own `try/except` and sets `source_ok[source] = False` — but `search_craigslist()` and `search_facebook_marketplace()` both `except Exception: return []` (`scraper.py:169-172`, `211-214`), so the pipeline's handler almost never fires. See §2 F1. |
| Degradation-aware safety | **Done** | Delisting coverage guard (`DELIST_MIN_COVERAGE`) refuses to interpret a truncated scrape as an emptied market |
| Trailing per-source volume | **Done** | `scan_logs.source_counts` written every scan; `_recent_source_average()` computes the mean over `DELIST_COVERAGE_WINDOW` |
| Enrichment quality signal | **Partial** | `scan_logs.enrichment_rate` exists for Craigslist only, and nothing alerts on it |
| Global staleness monitoring | **Done** | `scheduler.scan_health()` + hourly `_health_check()` + dashboard banner. Ages from `started_at`, not `completed_at` (`scheduler.py:65`) — see §3.9 |

The gap is narrow and specific: **all of the monitoring is global, and none of it is
per-source.**

---

## 2. The failure modes we are actually defending against

Ordered by likelihood, which is not the same as ordered by severity.

**F1 — Succeeded-empty. This is worse than it first looks.**

An Apify actor hits a login wall or an interstitial, completes normally, and returns
an empty dataset. No exception is raised. Today:

```python
per_source["facebook_marketplace"] = fb_listings   # [] is perfectly fine here
source_ok["facebook_marketplace"] = True           # ← still True
```

But there is a second, larger mouth to the same funnel. **The scraper functions
swallow every exception:**

```python
# scraper.py:169-172 (and identically at 211-214)
    except Exception:
        logger.exception("Craigslist scrape failed")
        return []
```

So an Apify outage, an expired token, a 403, a network timeout — all of them return
`[]` to `run_scan()`, which records `source_ok = True` and a row count of zero. The
per-source `try/except` in `pipeline.py` is very nearly dead code: it can only fire
for an error raised *outside* the scraper function.

The practical effect is that **essentially every Craigslist and Facebook failure mode,
loud or quiet, currently arrives as "succeeded, zero rows"** — and global
`scan_health()` stays green as long as the other source works. This is both the most
likely way we break and the least visible.

It also means §3.4's rule 2 ("raised an exception → `down`") is worthless unless we
first make the scraper distinguish *empty* from *failed*. That is phase (a0) below and
it is a prerequisite for everything else in this spec, not an optional cleanup.

**F2 — Partial breakage.** The source still returns rows, but a field we depend on
stops arriving. If Facebook stops returning `description`, Gemini has nothing to
analyze and quietly starts returning "unknown" for everything. Detection collapses
while every dashboard number looks normal. More common than F1 and harder to see.

**F3 — Volume collapse.** Row counts drop to 20% of normal — pagination broke, or a
rate limit is truncating results. Coverage is now poor but non-zero.

**F4 — Upstream actor change.** `automation-lab/craigslist-scraper` and
`apify/facebook-marketplace-scraper` are unpinned. An author pushing a breaking
change lands in production without any action on our part.

**F5 — Hard block.** Exceptions, HTTP 403s, captchas. *Believed* handled — but per F1
these are currently swallowed inside the scraper and reach the pipeline as an empty
list, so today the loud failure is disguised as a quiet one. Fixing F1 is what makes
F5 genuinely handled.

---

## 3. Design

### 3.1 Prerequisite: let the scrapers report failure (a0)

Per §2 F1, `search_craigslist()` and `search_facebook_marketplace()` currently
`except Exception: return []`. Every classification rule below that depends on knowing
whether a source *failed* is inert until that changes.

The fix is deliberately minimal — do **not** let exceptions propagate, because the
per-source isolation in `run_scan()` is worth keeping and callers rely on getting a
list back. Instead return the failure alongside the rows:

```python
class SourceResult(NamedTuple):
    rows: list[dict]
    ok: bool
    error: str | None
```

Both search functions return a `SourceResult`. `run_scan()` reads `.ok` into
`source_ok` instead of hardcoding `True`, and `.error` into `errors`. Nothing
downstream of `run_scan()` changes, because it still extends `all_listings` with
`.rows`.

This is the one place in the spec that touches the scraper contract, and it is the
highest-value change in the whole milestone: it converts the majority of our failure
modes from invisible to visible on its own, before any of the health machinery exists.

### 3.2 New module: `source_health.py`

Pure functions, no web-layer imports, same reasoning that pushed `run_scan` out of
`routes.py` into `pipeline.py` — the scheduler needs this and must not import FastAPI.

```
source_health.py
    SOURCE_FIELD_FLOORS      : dict[str, dict[str, float]]
    field_completeness(rows) -> dict[str, float]
    classify_source(...)     -> dict          # one source, one verdict
    source_health(session)   -> dict[str, dict]   # all known sources
```

`scheduler.scan_health()` composes this in; `routes.py` just serializes it.

### 3.3 Persistence — no migration required

We extend the *value* type inside the existing `scan_logs.source_counts` JSON rather
than adding a column. Today it is `{"craigslist": 42}`. It becomes:

```json
{
  "craigslist": {
    "rows": 42,
    "ok": true,
    "fields": {"description": 1.0, "street_address": 0.98, "latitude": 0.98},
    "actor": "automation-lab/craigslist-scraper",
    "build": "0.1.23",
    "duration_s": 61.4
  },
  "facebook_marketplace": {"rows": 0, "ok": true, "fields": {}, "...": "..."}
}
```

Legacy rows hold a bare int. `_recent_source_average()` gains a four-line tolerant
reader and keeps working on both shapes:

```python
def _row_count(value):
    """source_counts values were bare ints before M1.5 and are objects after."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return value.get("rows")
    return None
```

**Why not a new column:** a second column would duplicate the row count and give us
two things to keep in sync, and the delisting guard that reads `source_counts` is
covered by 49 passing checks that I would rather not disturb. One source of truth,
one tolerant parser, zero Alembic revisions.

Separately, `ScanLog.to_dict()` does **not** currently emit `source_counts`. It needs
to, or the Scans tab can't show any of this.

And `source_ok` in `run_scan()` is in-memory only — computed at lines 580/584/591/595,
consumed locally, then discarded when the function returns. Folding it into
`source_stats` is what makes "did this source fail?" answerable after the fact, which
rule 2 of the classifier depends on — though only once §3.1 makes the value mean
anything.

### 3.4 Classification

Four states. `unknown` is a real state and must not be collapsed into `ok` — "we have
never successfully scanned this source" is different from "this source is fine."

| State | Meaning | Rule |
|---|---|---|
| `down` | Not seeing this platform at all | Source reported `ok=False`, **or** `rows == 0` while trailing average > 0 |
| `degraded` | Seeing it partially, or with degraded data | `rows < SOURCE_DEGRADED_COVERAGE × average`, **or** any field below its floor |
| `ok` | Within normal bounds | Everything else with history |
| `unknown` | No basis for a verdict | No trailing history, or source never attempted |

Evaluation order matters and is deliberate:

1. **Never attempted this scan** → `unknown` (a Craigslist-only manual scan must not
   report Facebook as down)
2. **Source reported `ok=False`** → `down` (F5). Requires §3.1 — until the scrapers
   stop swallowing exceptions this rule can never fire.
3. **`rows == 0` and trailing average > 0** → `down` — this is F1, the whole point of
   the exercise. Succeeding with an empty dataset is a failure, not a quiet market.
4. **No trailing average** → `unknown` if `rows == 0`, else `ok`. Same posture as the
   delisting guard: with no history we decline to draw a conclusion.
5. **`rows < SOURCE_DEGRADED_COVERAGE × average`** → `degraded` (F3)
6. **Any measured field below its floor** → `degraded` (F2)
7. Otherwise → `ok`

Each verdict carries `reason` (human-readable, goes straight into the UI and the
alert), `rows`, `average`, `ratio`, and `failing_fields`.

Two sources of hysteresis, because a monitoring product that cries wolf gets ignored:

- Classification runs against the **trailing average**, already smoothed over
  `DELIST_COVERAGE_WINDOW` scans.
- A source must be non-`ok` for `SOURCE_UNHEALTHY_STREAK` (default 2) consecutive
  attempted scans before the hourly check escalates. The UI shows the current state
  immediately; the *page* waits for confirmation.

### 3.5 Field floors

We measured these on 2026-08-22 and then wrote them down as prose in the project
context, which is the wrong place for them. They belong in code as assertions.

```python
SOURCE_FIELD_FLOORS = {
    # Craigslist search rows carry 0% descriptions and no address at all;
    # street_address / latitude are added by craigslist_detail.py. These floors
    # are therefore only meaningful *after* enrichment has run, which is why
    # completeness must be measured at the end of the source block in
    # run_scan(), not on the raw scraper output.
    "craigslist": {
        "title": 0.98, "url": 0.98, "price": 0.80,
        "description": 0.90,      # measured 100% post-enrichment
        "street_address": 0.85,   # measured 98% post-enrichment
        "latitude": 0.85,         # measured 98% post-enrichment
    },
    "facebook_marketplace": {
        "title": 0.98, "url": 0.98, "external_id": 0.98,
        "description": 0.90,      # measured 100%
        "image_urls": 0.90,       # measured 100%
        "location": 0.75,         # measured 91%
        # street_address is structurally absent on FB — 0%. Deliberately absent
        # from this table; asserting a floor on it would flag permanently.
    },
}
```

Field names are the keys of the normalized dict from `scraper._build_listing()`
(`source`, `external_id`, `title`, `price`, `location`, `description`, `url`,
`image_urls`, `posted_date`) plus the keys enrichment adds (`street_address`,
`latitude`, `longitude`, `enriched`). Completeness counts a field as present when
it is not `None` and not an empty string.

Floors sit meaningfully below measured rates so that normal variance doesn't flap.
A field absent from a source's table is never checked — that is how we encode
"structurally unavailable" as distinct from "broken."

Completeness is computed over the rows the source actually returned; a source with
`rows == 0` reports `{}` and is caught by the volume rules instead.

### 3.6 Typed ingestion (Pydantic at the connector boundary)

Field-completeness floors catch *missing* data. They do not catch data that is
present and wrong-shaped — a `price` that arrives as `"$1,200/mo"` instead of a float,
an `image_urls` that becomes a list where it was a comma-joined string. That drift
reaches the detector as garbage and produces a confident wrong answer rather than an
error.

We already use Pydantic for the Gemini response (`FraudAnalysisResult`) and for every
API request body. The one place it is absent is the boundary where untrusted data
actually enters: `scraper._build_listing()` returns a bare dict.

Add a `ScrapedListingIn` model validated at the end of `_build_listing()`. Rejected
items are counted per source, not silently dropped — a rising validation-failure rate
is itself a degradation signal and should feed `classify_source()` alongside the
completeness floors. Two distinct questions, two distinct signals:

| | Catches | Signal |
|---|---|---|
| Schema validation | Wrong *type* or shape | `validation_failures` count |
| Completeness floors | Missing *values* | `fields` rates |

### 3.7 Retry, backoff, and a circuit breaker

There is currently **no retry anywhere** — not in `scraper.py`, not in
`craigslist_detail.py`. The only sleep in the codebase is the politeness delay between
Craigslist detail fetches.

This matters more once §3.4 exists than it does today: a single transient blip
currently costs us one thin scan, but under per-source health it would classify a
source as `down` and page someone. **Retries must land before or with the health
work, or the health signal will be noisy enough that people learn to ignore it** —
which is the exact failure this whole milestone is trying to prevent.

Minimum: three attempts with exponential backoff and jitter on the Apify call and on
each Craigslist detail fetch; distinguish retryable (timeout, 5xx, 429) from terminal
(401, 403) and do not retry the latter. A circuit breaker that stops attempting a
source after a sustained failure rate is worth having for cost control on a metered
API, but it is second-order — note it and defer.

Dead-letter storage for individual unparseable items, and synthetic canary jobs
against a known-static listing, both came out of the research as sensible at larger
scale. At two sources with a trailing-average baseline they earn their keep less
clearly than the above. Deferred, deliberately.

### 3.8 Actor pinning (F4)

```python
CRAWLER_ACTOR_ID    = "automation-lab/craigslist-scraper"
CRAWLER_ACTOR_BUILD = APIFY_CRAIGSLIST_BUILD   # config, default None = latest
```

Pass `build=` to `client.actor(...).call()` when set, and always read the resolved
build back out of the run metadata (`run["buildNumber"]`) into `source_stats`. Even
unpinned, recording the build means a breakage can be correlated with an upstream
release instead of guessed at.

Pinning is opt-in per source rather than mandatory: pinning trades "breaks without
warning" for "silently stops receiving fixes." Recording the version is unambiguously
good; pinning is a judgement call we want to be able to make per source, at runtime,
without a deploy.

### 3.9 API

`/api/scans/health` gains a `sources` map:

```json
{
  "healthy": false,
  "reason": "facebook_marketplace down",
  "last_success_at": "2026-08-23T04:00:00Z",
  "hours_since_success": 2.1,
  "sources": {
    "craigslist": {
      "status": "ok", "rows": 42, "average": 44.6, "ratio": 0.94,
      "failing_fields": [], "last_ok_at": "...", "reason": "ok"
    },
    "facebook_marketplace": {
      "status": "down", "rows": 0, "average": 38.2, "ratio": 0.0,
      "failing_fields": [], "last_ok_at": "2026-08-14T04:00:00Z",
      "reason": "returned 0 rows against a 38.2 average — scrape succeeded empty"
    }
  }
}
```

**Breaking change to note:** top-level `healthy` becomes
`freshness_ok AND no source is down`. `DashboardTab.tsx:17` gates the banner on
`const showHealthBanner = health && !health.healthy;` — a truthiness check, not a
strict `=== false` — so it will begin firing for per-source outages. That is the
intent, but it changes existing behaviour and the banner copy needs to name the source
rather than saying "scanning may be stale."

**Minor bug to fix while we're in here:** `scan_health()` computes staleness from
`last_ok.started_at` (`scheduler.py:65`), not `completed_at`. A long scan is therefore
reported as older than it is, and a scan that starts but never finishes still advances
the clock as far as this calculation is concerned. Should be `completed_at` with
`started_at` as a fallback.

### 3.10 UI — coverage widget

New card on the Dashboard, above the fold, always visible — not conditional the way
the current health banner is. A status display that only appears when something is
wrong trains the customer to distrust its absence.

```
Coverage                                    last scan 2h ago
  Craigslist            ● OK        42 rows   (avg 45)
  Facebook Marketplace  ● Down       0 rows   (avg 38)
                        └ scrape succeeded empty since Aug 14
```

Green / amber / red per source, row count against trailing average, and the `reason`
string underneath anything not green. The Scans tab gains per-source columns from the
same `source_counts` payload.

This is the piece that converts a liability into a trust signal. A customer who can
see "FB degraded, we know, here's since when" is being told the truth by a product
that is working. A customer who discovers it themselves has caught us not looking.

### 3.11 Escalation

`scheduler._health_check()` runs hourly and already escalates staleness to us and
never to the customer. It gains per-source escalation on the same terms, gated by
`SOURCE_UNHEALTHY_STREAK` so a single bad scan doesn't page anyone.

Customer-facing alerting about coverage stays out of scope. The `OBSERVE_MODE` and
`TWILIO_ENABLED` gates cover fraud alerts, and mixing operational alerts into that
path would put "your scraper is broken" through the same SMS budget as "someone is
impersonating your property."

---

## 4. Config

| Flag | Default | Meaning |
|---|---|---|
| `SOURCE_DEGRADED_COVERAGE` | `0.5` | Below this fraction of trailing average → `degraded` |
| `SOURCE_HEALTH_WINDOW` | `5` | Scans in the trailing average (mirrors `DELIST_COVERAGE_WINDOW`) |
| `SOURCE_UNHEALTHY_STREAK` | `2` | Consecutive non-`ok` attempted scans before escalation |
| `APIFY_CRAIGSLIST_BUILD` | `None` | Pin the Craigslist actor build; `None` = latest |
| `APIFY_FACEBOOK_BUILD` | `None` | Pin the Facebook actor build; `None` = latest |

Note `SOURCE_DEGRADED_COVERAGE` (0.5) is deliberately *looser* than
`DELIST_MIN_COVERAGE` (0.6). Delisting is destructive and should be conservative;
reporting degradation is not. They answer different questions and should not share
a constant.

---

## 5. Tests — `test_source_health.py`

Table-driven over `classify_source()`, since the whole thing is a decision table:

1. Source function returned `ok=False` → `down`
2. **`rows == 0`, `ok == True`, average 38 → `down`** — F1, the headline case
3. `rows == 0`, no history → `unknown`, not `down`
4. `rows > 0`, no history → `ok`
5. `rows = 10`, average 40 → `degraded` (ratio 0.25)
6. `rows = 40`, average 44 → `ok`
7. FB `description` at 0.4 against a 0.90 floor → `degraded`, `failing_fields == ["description"]`
8. FB `street_address` at 0.0 → `ok` (structurally absent, no floor defined)
9. Source not attempted this scan → `unknown`
10. Legacy bare-int `source_counts` still parses; `_recent_source_average` unchanged
11. Streak logic: one bad scan does not escalate, two consecutive do
12. `/api/scans/health` shape — `sources` present, every source has the full key set

Plus, for the §3.1 prerequisite specifically:

13. A scraper raising internally yields `SourceResult(rows=[], ok=False, error=...)`
    rather than a bare `[]` — this is the regression that currently has no coverage and
    is the reason the bug survived M1's review.

Plus a regression check that the existing **45** `check(...)` assertions in
`test_pipeline.py` still pass against the new `source_counts` shape. That suite is the
reason we chose a tolerant parser over a migration, so it is the thing that proves the
choice was sound.

---

## 6. Phasing

| | Work | Size | Notes |
|---|---|---|---|
| **a0** | **`SourceResult` — scrapers report failure instead of returning `[]`** | **S** | **Prerequisite for everything else (§3.1). Highest value-per-line in the milestone** |
| a | `source_health.py`: floors, `field_completeness`, `classify_source` | S | Pure functions, fully unit-testable, no DB |
| b | Write rich `source_stats` in `run_scan()`; tolerant `_row_count` reader; `to_dict()` emits `source_counts` | S | No migration |
| c | Compose into `scan_health()`; extend `/api/scans/health`; per-source escalation; fix the `started_at` staleness bug | S | Breaking change to top-level `healthy` |
| d | Dashboard coverage widget + Scans tab per-source columns | M | The customer-visible half |
| e | Record and optionally pin Apify actor builds | S | Recording first, pinning later |
| f | Best-effort coverage language in `property-firm-brief.md` | prose | Do this **first** — see §7 |
| g | Retry + backoff around the Apify and detail-fetch calls (§3.7) | S | Land **with or before** (c), or the health signal is noisy from day one |
| h | `ScrapedListingIn` Pydantic model at the connector boundary (§3.6) | S | Cheap once (a0) exists — the failure has somewhere to be reported |
| i | Circuit breaker on repeated source failure (§3.7) | S | Only worth it after (g) and (b) give it something to count |

(a0) is worth shipping on its own even if nothing else in this milestone gets built:
it converts the majority of our failure modes from silent to visible for maybe thirty
lines. (a)–(c) are then mostly wiring up data we already write. (d) is the real work.
(g)–(i) came out of the table-stakes research (2026-08-23) and are ordered by what
they depend on rather than by size.

---

## 7. Commercial posture

Engineering can't answer this on its own, but the spec should say what it assumes.

**Coverage is best-effort, and we say so before anyone signs.** Not "we monitor every
scam on every platform," but "we monitor these platforms continuously, we detect and
disclose degradation, and we target restoration within a stated window." That
paragraph belongs in `property-firm-brief.md` now, while it is a description of how
we work, rather than after an outage, when it reads as an excuse. This is why (f) is
sequenced first despite being the smallest item.

**The sources are not equally fragile, and the language should not pretend they are.**

- **Craigslist** — we own the detail fetch outright. Plain httpx, browser UA, 1.5s
  delay, measured 50/50 success with no blocks and no proxy. When it breaks we can
  usually fix it ourselves, same day. The Apify search actor is the fragile half, and
  it is replaceable.
- **Facebook** — a single third-party actor on a hostile platform, no detail
  enrichment, no proxies, ZIP+4 as the only geographic hook. When it breaks we are
  waiting on someone else. We should not promise the same restore window for both.

That asymmetry is an argument for a second Facebook actor as failover before it is an
argument for deepening our investment in the one we have.

**We can write and publish our own Apify actors — and sell them.** This reframes the
Facebook dependency from a liability into an option we have not exercised.

- There is not one canonical Facebook Marketplace actor. Several exist, they are
  maintained at different cadences, and the one we use
  (`apify/facebook-marketplace-scraper`) is updated regularly *by someone else*. Every
  one of those updates is an unannounced change to our input contract — which is the
  case for recording the build hash (phase e) independent of everything else here.
- Multiple actors is a **failover** story first: two actors, same normalized
  `_build_listing()` contract, second one runs when the first returns `ok=False` or
  falls below the degraded floor. Cheap, because the contract already exists.
- Publishing our own is a **control** story: we set the update cadence, we know why it
  broke, and the restore window stops being someone else's decision. It costs us the
  maintenance we are currently renting.
- Selling it is a **revenue** story, and a strategically consistent one — the whole
  premise of §0 is that connector upkeep is the durable work. An actor on the Apify
  store monetises that upkeep against a market wider than our own customer base, and
  its usage tells us it broke before our own scan does.

Sequencing: none of this is M1.5. It is only sane after (a0) and (b), because building
an actor without per-source telemetry means maintaining it blind. Recorded here so the
decision is made deliberately rather than by drift.

**Open question with legal consequences: does the Facebook actor authenticate?** We
have not checked. Post-*hiQ* and *Van Buren*, scraping public data is largely defused
under the CFAA; the bright line is logging in. If the actor uses session cookies or a
burner account, we are on the other side of that line and inside Facebook's ToS as a
contract matter regardless of the CFAA. This has to be answered before we sign a
customer, and it is a hard requirement on any actor we publish ourselves.

**What the subscription buys**, stated plainly: continuous cross-platform monitoring,
matching and alerting, *and ongoing connector maintenance* — the last being the line
item that recurs. The detection engine is largely finished. The treadmill is not.

---

## 8. Explicitly out of scope

Recorded so we don't relitigate:

- **Multi-actor failover, and publishing our own actor.** Right idea, out of scope
  *for M1.5* — see §7. Both need per-source telemetry (a0, b) to exist first.
- **Proxies, captcha solving, headless browsers.** Only relevant for Facebook detail
  enrichment, which we have not attempted. Large cost, no measured need.
- **Customer-facing coverage SMS/email.** §3.11. Operational alerts do not belong in
  the fraud-alert budget.
- **Historical uptime reporting / status page.** Wants the per-source data to exist
  first. Natural follow-on once (b) has been writing `source_stats` for a while.
- **Automated actor-change detection.** Recording the build (e) is the cheap 80%.

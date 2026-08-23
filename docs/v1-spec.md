# fillory fraud detector — v1 Product Specification

**Status:** proposed
**Supersedes:** v0 (see `system-spec.md`)
**Date:** 2026-08-22
**Author:** Fillory

---

## 1. Framing

**v0** — built 2026-08-22 — proved the hard technical question: *can we reliably
tell whether a marketplace listing is impersonating a specific property?* Yes.
Four matching signals, a 17-case regression suite, measured source data, and a
false-positive rate we actively engineered down.

v0 is a **detection engine with a dashboard**. It is single-tenant, runs only
when a human clicks, alerts to one hardcoded phone number, and does nothing
about what it finds.

**v1** turns that engine into a **product that runs itself**: multi-tenant,
scheduled, self-serve, and — critically — one that closes the loop by getting
fraudulent listings *removed*.

### The one-sentence goal

> A property management firm signs up, adds their properties, and from then on
> fraudulent listings impersonating them are detected, evidenced, and taken
> down — without anyone at Fillory touching it.

### What defines success

v1 is done when the design-partner firm has run for **30 consecutive days with
zero Fillory intervention**, and we can show them a list of listings that were
removed because of us.

---

## 2. Principles

1. **Silence is the default.** A false alarm costs more than a missed
   detection. Every threshold biases toward "unknown."
2. **Never automate anything that requires a legal attestation.** See §8.
3. **Never automate interaction with a platform that forbids it.** Detection
   scraping is a calculated, bounded risk; enforcement automation is not.
4. **Evidence is the product.** A detection nobody can act on is worthless.
   Every finding must arrive with proof attached.
5. **Measure, don't assume.** v0's biggest wins came from measuring our own
   data and disproving both internal beliefs and web consensus. Keep doing it.

### Explicit non-goals for v1

- Detecting rental scams generally. We protect *registered* properties only.
- Zillow, Apartments.com, OfferUp, Nextdoor. Craigslist + Facebook first.
- Mobile apps.
- Anything that requires a Fillory human in the loop per-listing.

---

## 3. The gap: v0 → v1

| Area | v0 | v1 |
|---|---|---|
| Tenancy | Single, implicit | Multi-tenant orgs with isolation |
| Auth | None | Neon Auth, roles, invites |
| Onboarding | Manual DB rows | Self-serve wizard |
| Scanning | Manual click | Scheduled, per-org, multi-market |
| Dedup | None | `external_id` upsert + listing identity |
| Alerting | 1 hardcoded phone | Per-org routing, email + SMS, digests, dedup |
| Detection | 4 address signals | + image pHash (layer 3) |
| Enforcement | None | Evidence pack → DMCA pipeline → status tracking |
| Coverage | Fixed queries, Portland | Per-org markets, measured recall |
| Observability | Log files | Health, metrics, failure alerts, cost tracking |
| Cost | Unknown | Metered per org |
| Firm's own listings | Would be flagged as fraud | Authorized-poster allowlist + property lifecycle |
| Being wrong | No path | Retraction, counter-notice handling, wrongful-filing metric |
| Source risk | Unmanaged | Schema validation, hot-spare actors, health per dependency |
| Migrations | Hand-written `ALTER TABLE` strings | Alembic |
| Testing | Manual local runs | CI on every push + nightly |
| Environments | Laptop only | Staging + production Neon branches |

---

## 4. Multi-tenancy

### Model

```
Organization  (the property management firm)
  └── Users            (staff, via Neon Auth; roles: owner / manager / viewer)
  └── Markets          (city+state pairs to scan)
  └── Properties       (protected units)
        └── PropertyImages  (+ perceptual hashes)
  └── Listings         (scraped, scoped to org via market)
  └── Cases            (a confirmed impersonation + its enforcement lifecycle)
  └── AlertRoutes      (who gets told, how)
  └── Authorization    (signed agent authorization — see §8)
```

**Auth:** Neon Auth (already provisioned — `DBFB9343D8_NEON_AUTH_URL`). No
custom auth. Roles:

| Role | Can |
|---|---|
| `owner` | Everything, incl. billing and signing authorizations |
| `manager` | Properties, review cases, approve takedowns |
| `viewer` | Read-only |

### Isolation

Every tenant-scoped table carries `org_id NOT NULL`. Enforcement is at the
query layer via a mandatory session-scoped filter, plus Postgres **row-level
security** as a second line of defence. A missing `org_id` filter must fail
closed, not leak.

> **Note on scraped listings.** Listings are *not* inherently org-scoped — two
> firms in Portland scan the same marketplace. Store listings **once per
> market**, deduplicated, and let each org's matcher run against the shared
> pool. This avoids N× scraping cost and is the single biggest cost lever in
> the system. Only *matches* and *cases* are org-scoped.

This is an architectural change from v0, where listings are global-implicit.

---

## 5. Scanning

### Scheduler

APScheduler in-process for v1 (Celery/Arq only if we outgrow it).

- Per-market scan cadence, default **every 4 hours**, configurable.
- Jitter to avoid thundering-herd against Apify.
- One scan per market — **not** per org. Orgs sharing a market share the scan.
- Missed-run catch-up, with a cap so an outage doesn't cause a stampede.
- Scans are idempotent; a re-run must not duplicate rows.

### Deduplication *(fixes a v0 defect)*

```sql
UNIQUE (source, external_id)
```

Upsert on conflict: update `last_seen_at`, price, title; never re-insert.
Add `first_seen_at`, `last_seen_at`, `times_seen`, `delisted_at`.

Why it matters beyond storage: without this, the same fraudulent listing is
re-detected every scan and re-alerts every 4 hours. That alone would make the
product unusable.

Craigslist currently has 100 rows from 2×50 scans — 50 of them duplicates.
A backfill migration must collapse these.

### Coverage — an open measurement task

v0 never established what fraction of the market we actually see. Apify
returns ~50 Craigslist / ~90 Facebook rows per scan against fixed queries.

v1 must measure recall before claiming coverage:

1. Enumerate a market's full rental inventory over 24h via multiple query
   strategies (price bands, bedroom counts, neighbourhood subdivisions).
2. Compare against a single broad query.
3. Report coverage as a number, per market, on the dashboard.

**If we cannot state our coverage, we cannot honestly sell detection.**

### Multi-market

`SCRAPE_CITY` / `SCRAPE_STATE` globals are replaced by a `markets` table.
A firm with units in Portland and Vancouver WA gets both scanned.

### Source resilience and vendor risk

The entire product sits on top of two data sources we do not control, one of
which we reach through a third party. This is the single largest operational
risk in v1 and v0 has no mitigation at all.

| Risk | Mitigation in v1 |
|---|---|
| Apify actor is deprecated, breaks, or changes its output shape | Schema-validate every scrape result; a shape change fails loudly instead of silently returning zero rows. Keep a second actor identified per source as a hot spare. |
| Apify account limits / billing failure | Quota tracking with a threshold alarm; scans degrade to a reduced cadence rather than dying. |
| Craigslist starts blocking our self-hosted detail fetches | Measured today at 50/50 HTTP 200 with no proxy. Track the enrichment success rate per scan as a first-class metric; a sustained drop pages us. Proxy support behind a config flag, unused until needed. |
| Facebook detail pages remain unreachable | **Accepted limitation.** FB enrichment needs proxies or session cookies and was never attempted. We do not need it: measured FB data already gives 100% descriptions, 100% images, and ZIP+4 80% of the time. Do not build this unless a measurement says we must. |
| Nominatim rate limits or bans us | 1.1s throttle already enforced. v1 adds a persistent geocode cache keyed on the normalized address so we never ask twice, and a fallback provider behind an interface. |

**Rule:** every external dependency reports a health state, and "returned zero
rows" is treated as a failure to investigate, never as "no fraud found."

---

## 6. Detection

### Layer 3 — image perceptual hashing

The remaining detection gap, and the only signal that *proves* impersonation
rather than suggesting it. Full technical detail in
`layer3-image-hashing-spec.md`; summary of the v1 commitment:

- pHash + dHash via `imagehash`, stored per property image.
- Every scraped listing image hashed at scan time.
- Match rule: **any single image ≤6 Hamming distance, or two images ≤10.**
- Mirror-hash stored to catch horizontally flipped images.
- Stock-photo suppression: a hash matching images across many unrelated
  properties is blacklisted (floor plans, generic amenity shots).

Steps 1–5 of that spec are buildable **now** against the Killingsworth photos
and should not wait on the firm.

### Signal fusion

v0 takes the single strongest signal. v1 combines them into a confidence tier,
because *address + photos* is categorically stronger than either alone:

| Tier | Condition | Action |
|---|---|---|
| **Confirmed** | Image match **and** address/geo/ZIP+4 match | Auto-open case, alert immediately |
| **Probable** | Image match alone, **or** geo/address match + AI fraud verdict | Alert, human review before filing |
| **Possible** | ZIP+4 match + AI fraud verdict | Daily digest, no immediate alert |
| **Unknown** | Everything else | Stored, not surfaced |

Only **Confirmed** may proceed to enforcement without human review — and even
then, a human still signs (§8).

### The firm's own listings *(missing requirement, high severity)*

A registered property that is actually vacant will be advertised on Craigslist
and Facebook **by the firm itself** — with the correct address and the firm's
own photos. Under v0's logic that is a perfect match on every signal, including
layer 3. The first thing the system would do on a real portfolio is flag its
own customer's marketing.

v1 needs an explicit legitimacy allowlist before it ever sees a real portfolio:

- **Authorized posters** per org: known poster names, contact emails, phone
  numbers, and marketplace account URLs/IDs. A match against one of these
  resolves to `authorized`, not `fraud`, regardless of signal strength.
- **Syndication domains**: listings whose contact or apply link points at the
  firm's own site or its listing syndicators are authorized.
- **One-click teach**: reviewing a case offers "this is ours" — which records
  the poster identity as authorized for that org and re-resolves any other open
  case sharing it.
- **Property lifecycle**: `occupied` / `marketing` / `off-market`. A property in
  `marketing` expects legitimate listings and biases toward `authorized`; a
  property in `occupied` should have no listings at all, so any match there is
  materially more suspicious and escalates a tier.

This also improves detection quality rather than just suppressing noise: a
listing that matches the property but is *not* from an authorized poster while
the unit is occupied is close to a definitive impersonation.

### AI dependency and prompt regression

Gemini is reached via the Workshop proxy, and v0 already lost two model names
to deprecation (`gemini-2.5-flash-preview-04-17`, `gemini-2.0-flash`). Model
drift is a certainty, not a risk.

- Primary + fallback model configured; a hard failure resolves to `unknown`,
  never to `fraud`. *(Already true in v0 — keep it true.)*
- A **golden-set prompt regression suite** committed alongside
  `test_matcher.py`: fixed listings with expected verdict and confidence bands.
  Run it in CI and on every model change. A model swap that shifts calibration
  is a release blocker.
- Verdict distribution monitored in production; a sudden shift in the
  fraud/legitimate/unknown mix means the model changed under us.
- Cost and latency per call recorded, since the matcher is the gate that keeps
  AI spend proportional to matches rather than to listings.

### Recall validation *(currently zero)*

v0 has never caught a real scam. All verification is negative (no false
positives) or synthetic (pasted test scams). Before v1 ships:

1. Obtain **3–5 historical scam listings** from the design-partner firm. Free,
   perfectly calibrated, and the only true-positive data available.
2. Replay them through the pipeline. Every one must be caught.
3. Add them to `test_matcher.py` as permanent regression cases.

**This is a launch blocker.** Shipping a detector with unmeasured recall is
selling something we haven't verified.

---

## 7. Alerting

### Deduplication and suppression

- One alert per **case**, not per detection.
- Re-detection of an open case updates it silently.
- Material change (price change, new photos, relisted under new ID) → a single
  "case updated" notification.
- Hard rate limit per org per day, with overflow rolled into a digest.

### Routing

`AlertRoutes` per org: multiple recipients, each with channel (email / SMS),
severity floor (Confirmed only vs everything), and quiet hours. Optionally
scoped to a property or market, so a regional manager only hears about theirs.

Email is the primary channel — SMS-only was a v0 shortcut and no firm will
accept it. Email carries the evidence pack; SMS is a pointer.

### Twilio safe mode

`TWILIO_ENABLED` stays. v1 adds a per-org sandbox flag so a newly onboarded
org runs in observe-only mode for its first 7 days — detections recorded,
nothing sent — while we sanity-check its false-positive rate before it starts
paging people.

---

## 8. Enforcement — the part that delivers the value

### What the research established

I researched whether takedown filing can be automated. It cannot, and the
reasons are hard constraints, not obstacles to engineer around:

| Channel | Automatable? | Why |
|---|---|---|
| Craigslist flagging | **No** | Community moderation; ToU forbids bots; IP rate-limiting + fingerprinting; not a real takedown channel |
| Craigslist site interaction | **No — legally dangerous** | ToU prohibits automated access; they litigate (*v. 3Taps*, *v. PadMapper*) |
| Meta IP report forms | **No** | No public API; automated submission violates their Automated Data Collection Terms (*Meta v. Bright Data*) |
| DMCA notices generally | **Partially** | §512(c)(3) requires a signature under penalty of perjury by the owner or an authorized agent — a bot cannot attest |

**A bot cannot swear an oath.** Any design that has software certifying
ownership under penalty of perjury is both illegal and, if it ever misfires,
exposes us and our customer to liability for fraudulent takedown.

### What *is* legitimately automatable

The saving distinction: **emailing a DMCA notice to a platform's designated
agent is ordinary correspondence, not automated interaction with their
website.** Everything up to and including delivery can be automated. Only the
attestation needs a human.

### The v1 enforcement pipeline

```
Confirmed case
      │
      ▼
1. EVIDENCE PACK          fully automated
      │  archived screenshot (timestamped, hashed)
      │  side-by-side photo comparison w/ Hamming distances
      │  matched property record + ownership provenance
      │  listing metadata, first/last seen, price delta
      │  immutable audit record
      ▼
2. NOTICE GENERATION      fully automated
      │  §512(c)(3)-compliant DMCA notice, pre-filled
      │  platform-specific formatting
      ▼
3. HUMAN ATTESTATION      ← the ONLY manual step
      │  authorized signer reviews and clicks "I certify"
      │  one click, on mobile, ~15 seconds
      │  identity + timestamp + IP recorded
      ▼
4. DELIVERY               fully automated
      │  Craigslist: email to designated DMCA agent
      │  Meta: submitted via Brand Rights Protection
      ▼
5. TRACKING               automated
         listing re-polled; delisting detected and recorded
         escalation if still live after N days
         time-to-takedown reported per platform
```

### Who signs

The firm designates an **authorized signer** during onboarding, and signs a
standing **agent authorization** letting Fillory prepare and transmit notices
on their behalf. Both Craigslist and Meta accept authorized-representative
filings with documented authority.

This reduces the firm's per-takedown effort to one click, and it is the legally
correct structure — not a workaround.

### Meta Brand Rights Protection

For Meta specifically, BRP is the legitimate scaled channel: it supports bulk
actions **and image reference-file matching**, which complements our pHash work
directly. Onboarding each firm into BRP under their own Business Manager should
be part of the onboarding wizard. This is the closest thing to automated
enforcement that exists, and it's sanctioned.

### Honest expectation-setting

Copyright claims from the rights holder get actioned; generic "this is a scam"
reports get queued and frequently ignored. This is precisely why the photo ask
in the firm brief matters — **without owning the copyright, there is no
leverage.** Enforcement effectiveness is a direct function of how complete the
photo library is.

### When we are wrong — reversal and counter-notice

A wrongful takedown is the worst failure mode this product has. It harms an
innocent poster, exposes the firm to §512(f) liability for material
misrepresentation, and ends the customer relationship. It must be a designed
path, not an incident.

- **Pre-filing gate:** a notice can only be generated from a Confirmed case
  with a complete evidence pack. The attestation screen shows the signer the
  evidence, not a summary.
- **Retraction:** one action retracts a filed notice, sends a withdrawal to the
  same channel it was filed through, and freezes further action on the case.
- **Counter-notice handling:** if a poster counter-notices, the case moves to
  `disputed`, all automation on it stops, and the firm's signer plus Fillory
  are both notified. We never re-file on a disputed case automatically.
- **Post-mortem record:** every reversal is logged with the signals that
  produced it and, where the fault is ours, becomes a permanent regression case
  in `test_matcher.py` or the golden set.
- **Metric:** wrongful-filing rate is reported on the internal dashboard beside
  time-to-takedown. If it is not zero we stop filing.

---

## 9. Onboarding

Self-serve wizard, target **under 15 minutes**:

1. **Create org** — name, markets to monitor.
2. **Add properties** — CSV upload or manual. Address, unit, beds/baths, rent.
3. **Geocode + ZIP+4** — automatic. Geocoding already works; ZIP+4 should be
   looked up via the **USPS Address API** rather than asked for. *(v0's brief
   asks the firm to look these up manually — automating it removes a friction
   point and a data-entry error class. Manual entry stays as fallback.)*
4. **Photos** — drag-and-drop per property, or supply public listing URLs and
   we scrape them (the recommended option in the firm brief).
5. **Alert routing** — recipients, channels, quiet hours.
6. **Authorized posters** — the accounts, names, emails and phone numbers the
   firm advertises under, so we never flag their own marketing (§6).
7. **Authorization** — e-sign the agent authorization; designate signer.
8. **Observe mode** — 7 days of silent running, then a review before alerts go
   live.

---

## 10. Operations

### Observability

- `/health` covering DB, Apify, Gemini, scheduler liveness.
- Per-scan metrics: rows fetched, enrichment success rate, match counts by
  tier, AI calls, latency, cost.
- **Failure alerting to us, not the customer.** A scan that hasn't succeeded in
  12 hours pages Fillory. Silent failure is the worst outcome in a monitoring
  product — the customer assumes no news is good news.
- Detection-rate anomaly alarms: a market going from 40 listings to 2 means the
  scraper broke, not that fraud stopped.

### Cost model *(currently unknown — must be established)*

Per-org monthly cost drivers:

| Driver | Notes |
|---|---|
| Apify actor runs | markets × cadence — **shared across orgs in a market** |
| Craigslist detail fetches | self-hosted, bandwidth only |
| Gemini calls | matched listings only — the matcher is the cost gate |
| Image hashing | CPU + egress on listing photos |
| Neon | storage + compute |
| Twilio | per SMS |

Instrument every one of these per org from day one and surface them on an
internal dashboard. Pricing conversations require this number, and the
listings-shared-per-market design exists specifically to keep it low.

### Data retention

Define and publish: listings 12 months, evidence packs 7 years (legal record),
property photos retained only as hashes after processing.

### Engineering practice *(gaps carried from v0)*

None of this is glamorous, and all of it is load-bearing once a real customer
depends on the system running unattended.

| Gap in v0 | v1 requirement |
|---|---|
| Migrations are a hand-maintained list of `ALTER TABLE ... IF NOT EXISTS` strings in `database.py` | Move to **Alembic**. Additive-only strings cannot express a backfill, a constraint, an index, or the dedup collapse M1 needs — and they cannot be rolled back. |
| No CI | GitHub Actions on every push: `test_matcher.py`, the golden-set prompt suite, `tsc --noEmit`, ruff. A red build blocks merge. |
| Tests run only when I remember | Above, plus a nightly run against live-shaped fixtures to catch source drift. |
| One environment — the laptop | **Staging and production**, separate Neon branches. Never test a scraper change against production data. |
| No backups verified | Neon PITR confirmed and a restore actually rehearsed once. An untested backup is not a backup. |
| Not published; no deploy path | Publish via Workshop; document rollback. `TWILIO_ENABLED` flips only after the final URL is verified, and only after observe mode passes. |
| `uvicorn --reload` watching project files | Production runs without reload. Editing a `.py` file mid-scan currently aborts the scan — acceptable locally, fatal in prod. |
| Test data mixed with real data | Purge the Austin test property and the remaining `manual` row before the first customer, or move them to a seeded staging branch. Production must contain only real records. |
| No error tracking | Structured logging plus an exception tracker; unhandled exceptions in the scan pipeline must surface, not vanish into a log file. |

### Support and SLA

Before the design partner goes live, state plainly: expected scan cadence,
what we promise about detection (best-effort, coverage figure published, recall
measured), target time-to-notice, and how they reach a human. An unattended
system still needs a named owner when it breaks.

---

## 11. Security and legal

- Row-level security on all tenant tables; fail-closed query scoping.
- Secrets via connectors/keyring only — never in code, never in git.
- Audit log for: authorization signing, notice attestation, property changes,
  user invites.
- Scraping posture: detection scraping is bounded and polite (delays, backoff,
  caps, no auth bypass). Enforcement automation is prohibited by policy — §8
  is a **standing engineering constraint, not a preference.**
- Terms of service and DPA for customers.
- Counsel review of the agent-authorization template before first signature.

---

## 12. Milestones

| # | Milestone | Contents | Blocks |
|---|---|---|---|
| **M0** | *Foundation* | ~~Alembic~~ **done**; CI (matcher + golden set + tsc + ruff), staging branch, error tracking still open | — |
| **M1** | *Trustworthy* | ~~Dedup + upsert + backfill collapse, alert dedup by case, scheduler, failure alerting, observe mode, source health metrics~~ **done** | M0 for the migration |
| **M1.5** | *Resilient* | Per-source health, field-completeness floors, coverage widget, actor version pinning, best-effort coverage language — see `connector-resilience-spec.md` | M1 for `source_counts` |
| **M2** | *Verified* | Historical scam replay, recall measurement, coverage measurement, golden-set prompt suite | Needs firm's scam examples |
| **M3** | *Multi-tenant* | Orgs, Neon Auth, RLS, shared-listing refactor, alert routing, authorized-poster allowlist, property lifecycle | — |
| **M4** | *Proof* | Layer 3 pHash steps 1–5, then 6–7 | 6–7 need firm's photos |
| **M5** | *Enforcement* | Evidence packs, notice generation, attestation UI, delivery, tracking, retraction + counter-notice | Needs signed authorization + counsel |
| **M6** | *Self-serve* | Onboarding wizard, USPS ZIP+4 API, photo ingest, authorized posters capture, billing | — |
| **M7** | *Production* | Publish, backups rehearsed, enable Twilio, cost dashboard, retention, SLA, 30-day unattended run | All |

**Status (2026-08-23):** M0's Alembic work and all of M1 are built and
committed; see `system-spec.md` §3 for the behaviour as shipped. M0's CI,
staging branch and error tracking are not. **M1.5 (connector resilience) is
specified but not built** — `docs/connector-resilience-spec.md`. M2 is blocked
on the firm's historical scam examples.

**Recommended order:** M0 → M1 → M1.5 → M2 → M4(1–5) → M3 → M5 → M6 → M7.

Rationale: M0 is small and everything else is safer with it in place —
particularly M1, whose duplicate-collapse cannot be expressed as an additive
`ALTER TABLE`. M1 makes the current system non-annoying, M2 tells us whether it
actually works, and M4 steps 1–5 need nothing from anyone. M3's multi-tenancy
is a big refactor with no customer-visible value, so it comes after we've
confirmed the core is sound — but before M5, since enforcement needs org-scoped
authorization records. The authorized-poster allowlist rides with M3 because it
is org-scoped, and it **must** land before any real portfolio is loaded.

---

## 13. Open questions

1. **Do we have the design partner's scam history?** M2 is blocked on it and it
   is the highest-value free input available.
2. **Who signs?** Enforcement needs a named authorized signer at the firm and
   counsel review of the authorization template.
3. **Is Meta BRP available to the firm?** Requires Business Manager and
   verifiable rights. Determines whether Facebook enforcement is bulk or
   one-at-a-time.
4. **Pricing model** — per property, per market, or flat? Needs the cost
   numbers from §10.
5. **What happens on a false takedown?** Now specified in §8 (retraction,
   counter-notice, wrongful-filing metric) — what remains open is whether
   counsel wants a mandatory cooling-off period before filing.
6. **Does the firm want tenant-facing warnings?** A public "verify your listing"
   page is a plausible v2 surface and changes nothing in v1, but affects how we
   store evidence.
7. **How does the firm advertise vacancies?** Which accounts, which
   syndicators, which contact details. This populates the authorized-poster
   allowlist and is required before their portfolio is loaded — otherwise the
   first scan flags their own marketing.
8. **What is the acceptable alert volume?** A firm with 400 units in a scam-heavy
   market may generate more Confirmed cases than one signer can attest to. If
   so, enforcement needs batch attestation, which changes the §8 UI.

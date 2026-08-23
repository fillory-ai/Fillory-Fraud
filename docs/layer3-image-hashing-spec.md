# Layer 3 — Stolen-Photo Detection via Perceptual Hashing

**Status:** spec / not yet built
**Audience:** Matt + the property management firm
**Date:** 2026-08-22

---

## 1. Why this layer exists

Layers 1 and 2 (shipped) catch a scammer who publishes **your address**. They
are now strong on Craigslist: 100% of listings yield body text, 98% yield a
street address and coordinates.

They do not catch the other half of the problem. A competent rental scammer
frequently:

- posts **your photos** under a **different address** (so no address match), or
- posts your photos with **no address at all** (common on Facebook, which
  never exposes a street address for rentals), or
- deliberately misspells or omits the address to dodge exactly this kind of
  text matching.

Photos are the one asset the scammer *must* copy. A listing with no photos
does not rent. That makes the image the most reliable fingerprint we have —
and it is the only signal that works on Facebook Marketplace, where no street
address exists to match against.

**In one line:** Layers 1–2 ask "is this my address?" Layer 3 asks "are these
my photos?"

---

## 2. What we need from the property firm

This is the part requiring their cooperation. Everything else is our side.

### 2.1 The ask — original listing photos

For each protected unit, we need the **original, full-resolution photographs**
as published in their own marketing.

| Requirement | Detail | Why |
|---|---|---|
| Format | JPEG or PNG | Standard decoder support |
| Resolution | ≥ 800px on the long edge; originals preferred | Small thumbnails lose the detail hashing relies on |
| Quantity | 5–15 per unit; **every** photo used in public marketing | Scammers grab whichever photo looks best |
| Source | The exact files pushed to Zillow / Apartments.com / their own site | Must match what a scammer would scrape |
| Delivery | Zip, Dropbox/Drive link, or S3 bucket — one folder per unit | Simple, no integration work for them |
| Naming | `<unit-id>/<anything>.jpg` | Only the folder name needs to be meaningful |

### 2.2 What we do NOT need

Worth stating plainly, because it lowers their barrier to saying yes:

- No access to their PMS, CRM, or any internal system.
- No tenant data, lease data, or PII of any kind.
- No API integration, no credentials, no ongoing engineering effort.
- No live feed. A one-time drop per unit is enough; refresh only when they
  re-shoot a unit.

### 2.3 Ongoing process

One question to settle with them: **who sends new photos when a unit is
re-shot or a new unit is onboarded?** Options, easiest first:

1. **Manual drop** — they email/share a folder when photos change. Zero build
   cost. Relies on them remembering. Fine for a handful of units.
2. **Watched folder** — a shared Drive/Dropbox folder we poll nightly. Small
   build cost, no discipline required from them.
3. **Scrape their own listing pages** — we pull photos directly from their
   public marketing URLs on a schedule. No effort from them at all, and it
   guarantees we hash exactly what a scammer would see. Requires their written
   OK to scrape their own site.

**Recommendation: option 3, with option 1 as the fallback.** Option 3 is
self-maintaining and always in sync with what's publicly visible, which is
precisely the attack surface.

### 2.4 Permission we should get in writing

- Written permission to store and hash their marketing images.
- Confirmation they hold the copyright (usually they commissioned the
  photographer — worth confirming, because it also determines whether they can
  file **DMCA takedowns** against scam listings). This matters: a DMCA notice
  on stolen photos is generally the fastest way to get a fraudulent listing
  removed, faster than a fraud report. Photo ownership is the leverage.

---

## 3. How perceptual hashing works

A cryptographic hash (MD5/SHA) is useless here: change one pixel and the hash
changes completely. Scammers re-save, crop, and re-compress constantly.

A **perceptual hash** produces a short fingerprint from the image's visual
structure. Visually similar images produce similar hashes. Similarity is
measured as **Hamming distance** — the number of differing bits between two
64-bit hashes.

```
distance 0      identical file
distance 1–6    same photo, re-compressed / resized / lightly cropped   ← the scam case
distance 7–12   possibly related, needs review
distance > 12   different photo
```

### 3.1 The four easy options

These are the practical choices. All are mature, permissively licensed, and
runnable on the existing backend with no new infrastructure.

| # | Algorithm | Library | Speed | Robust to | Weak against | Verdict |
|---|---|---|---|---|---|---|
| 1 | **aHash** (average) | `imagehash` | Fastest | resize, compression | brightness/contrast changes | Too fragile alone |
| 2 | **pHash** (DCT) | `imagehash` | Fast | resize, compression, brightness, minor crop, watermarks | heavy crop, rotation | **Recommended default** |
| 3 | **dHash** (difference) | `imagehash` | Fastest | resize, brightness | heavy crop | Good cheap second opinion |
| 4 | **wHash** (wavelet) | `imagehash` | Slower | as pHash, slightly better on noise | rotation | Marginal gain over pHash |

Two heavier options if the simple ones prove insufficient:

| # | Approach | Library | Notes |
|---|---|---|---|
| 5 | **ORB / SIFT keypoints** | `opencv-python` | Survives heavy crop, rotation, overlaid text. ~100× slower; needs pairwise comparison. Use only as a second-stage confirmer on candidates. |
| 6 | **CLIP embeddings** | `open-clip-torch` or a hosted embedding API | Semantic similarity — catches "same room, different angle." Overkill and expensive; also produces false positives across similar-looking units. Not recommended. |

### 3.2 Recommendation

**Ship pHash + dHash together, flag on either.**

- One dependency: `pip install imagehash pillow` (`imagehash` is ~200 lines
  over Pillow/numpy, MIT licensed, stable for years).
- 64-bit hash per image, stored as a 16-char hex string.
- Hashing an image takes ~10ms. Comparing two hashes is a single XOR + popcount
  — effectively free.
- At our volume (≈150 listings/scan × ~5 photos = 750 images, against maybe
  100 property photos) that's 75,000 comparisons per scan, which is
  **microseconds**. The only real cost is downloading the listing images.

Escalate to ORB (option 5) only if we observe scammers defeating pHash with
aggressive crops. Do not start there.

---

## 4. Proposed implementation

### 4.1 Schema

```sql
CREATE TABLE property_image_hashes (
  id            UUID PRIMARY KEY,
  property_id   UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  image_url     TEXT,           -- source of the photo
  phash         CHAR(16),       -- 64-bit hex
  dhash         CHAR(16),
  created_at    TIMESTAMPTZ
);
CREATE INDEX ON property_image_hashes (property_id);

ALTER TABLE scraped_listings
  ADD COLUMN image_match_property_id UUID,
  ADD COLUMN image_match_distance    INTEGER,   -- best Hamming distance found
  ADD COLUMN image_match_count       INTEGER;   -- how many photos matched
```

### 4.2 Pipeline changes

1. **Ingest (one-off per unit)** — walk the delivered folder or the firm's
   public listing pages, hash every photo, store in `property_image_hashes`.
2. **Per scan** — for each listing, download up to 5 images (they're already in
   `image_urls`), hash them, compare against every stored property hash.
3. **Match rule** — flag when **either**:
   - any single image is within Hamming distance **≤ 6**, or
   - **two or more** images are within distance ≤ 10 (weaker individually, but
     two near-matches on the same listing is conclusive).
4. **Feed the result into the existing matcher** — an image match becomes a
   third signal in `_find_best_address_match`, ranked alongside geo proximity.
   Everything downstream (Gemini analysis, alerting, the UI) then works
   unchanged.
5. **Tell Gemini** — add the image evidence to the prompt, e.g.
   *"4 of this listing's 6 photos are pixel-near-identical to the registered
   property's marketing photos (Hamming distance 2–5)."*
   Stolen photos + a different address is a near-certain scam, and the model
   should weight it that way.

### 4.3 Cost and risk

- **Bandwidth** is the only meaningful cost: ~750 image downloads per scan.
  Mitigate by capping at 5 images/listing, hashing thumbnails where the source
  provides them, and caching hashes by image URL so repeat listings across
  scans are free.
- **False positives**: stock photos and floor-plan diagrams are reused across
  many unrelated listings and will collide. Mitigate by excluding
  floor-plan/logo images at ingest, and by requiring an image match to be
  corroborated by Gemini before alerting — never alert on a hash alone.
- **Rotation/mirroring**: pHash fails on a mirrored image. Cheap fix — also
  hash the horizontally flipped version of each property photo at ingest,
  doubling the stored hashes. Costs nothing at match time.

---

## 5. Suggested build order

| Step | Work | Depends on firm? |
|---|---|---|
| 1 | Schema + `imagehash` dependency | No |
| 2 | Hash ingest for images already on `properties.image_urls` | No |
| 3 | Listing image download + hash + compare in the scan pipeline | No |
| 4 | Wire image signal into matcher + Gemini prompt | No |
| 5 | UI: show matched photo pairs side by side in the detail dialog | No |
| 6 | Bulk ingest of the firm's real marketing photos | **Yes** |
| 7 | Ongoing refresh (option 1 / 2 / 3 above) | **Yes** |

Steps 1–5 can be built and validated immediately using the photos already
attached to your Killingsworth unit — we can prove the mechanism works, and
self-test by re-compressing and cropping your own photos to confirm the
distances land where this spec predicts. Only steps 6–7 need the firm.

---

## 6. The one question to put to the firm

> "Can you send us the original marketing photos for each unit you want
> monitored — or give us written permission to pull them from your public
> listing pages — and confirm you own the copyright so we can file takedowns
> when we find them stolen?"

Everything else in this document is our side of the work.

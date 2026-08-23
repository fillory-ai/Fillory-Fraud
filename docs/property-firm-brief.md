# What We Need From You — Property Management Firm Brief

**Prepared by:** Fillory
**Date:** 2026-08-22
**Purpose:** Two specific asks that let us protect your listings from rental scammers

---

## The short version

We monitor Craigslist and Facebook Marketplace for listings that impersonate
the units you manage, and alert you when we find one.

To do that well we need two things from you:

| # | Ask | Effort | Unlocks |
|---|---|---|---|
| **1** | **ZIP+4 code** for each protected unit | ~1 minute per unit, one time | Detection on Facebook Marketplace |
| **2** | **Original listing photos** for each protected unit | One folder drop, one time | Detection of stolen photos anywhere |

Neither requires access to your systems, your tenant data, or any engineering
work on your side.

---

## The problem, briefly

A scammer copies your listing — the address, the photos, the description —
reposts it at a below-market rent, and collects deposits and application fees
from people who will never receive a key. The victim's first contact with you
is usually a phone call asking why their key doesn't work.

The cost lands on you: reputation damage, angry calls, staff time, and
sometimes legal exposure. The scam is cheap to run and currently near-free of
consequences.

We catch these listings, usually within hours of posting.

---

## Ask 1 — ZIP+4 codes

### What we're asking for

The full 9-digit USPS ZIP code for each protected unit. Not the 5-digit ZIP
you already gave us — the extended one.

```
5-digit:   97218
ZIP+4:     97218-1234
                └──┘ this part
```

### Why it matters

Facebook Marketplace **never publishes a street address for rental
listings**. We measured this across 90 real Portland-area listings: zero
percent had a street address field. That means our normal address matching —
which works on 98% of Craigslist listings — has nothing to grab onto.

But Facebook does publish a ZIP+4 for about 80% of listings. And a ZIP+4 is
far more precise than people assume:

- A **5-digit ZIP** covers a whole neighbourhood — tens of thousands of
  addresses. Useless for identifying a specific building.
- A **ZIP+4** covers roughly **one side of one block**, or in many cases a
  **single building**.

So when a Facebook listing reports the same ZIP+4 as one of your units, that
listing is at your front door. It isn't proof on its own — we treat it as a
strong signal, not a verdict, and it still goes through fraud analysis before
anyone is alerted. But without it we are effectively blind on Facebook.

We also confirmed Facebook's location data is honest rather than deliberately
blurred: we took listings that happened to include a real street address in
the description, geocoded that address independently, and compared it to
Facebook's reported location. **Eleven out of eleven agreed.** The data is
trustworthy.

### How to get it — 1 minute per unit

1. Go to **https://tools.usps.com/zip-code-lookup.htm**
2. Choose **"Find by Address"**
3. Enter the full street address including unit number
4. USPS returns the ZIP+4, e.g. `97218-1234`

### What to send us

A simple list or spreadsheet:

| Unit / Property name | Full street address | ZIP+4 |
|---|---|---|
| 3BR Townhome | 4411 NE Killingsworth St Unit 107, Portland, OR | 97218-____ |
| … | … | … |

> **Important:** if a building has multiple units, please check whether USPS
> gives each unit a different ZIP+4 or one shared code for the building. Both
> are normal. Either works for us — we just need to know which, so we can set
> expectations about whether a match means "this building" or "this unit."

---

## Ask 2 — Original listing photos

### What we're asking for

The original, full-resolution photographs for each protected unit, exactly as
published in your own marketing.

| Requirement | Detail | Why |
|---|---|---|
| **Format** | JPEG or PNG | Standard decoder support |
| **Resolution** | ≥800px on the long edge; originals strongly preferred | Small thumbnails lose the detail the technique relies on |
| **Quantity** | 5–15 per unit — **every** photo used in public marketing | Scammers grab whichever photo looks best, not a predictable one |
| **Source** | The exact files pushed to Zillow / Apartments.com / your own site | Must match what a scammer would actually scrape |
| **Delivery** | Zip file, Dropbox/Drive link, or S3 bucket — one folder per unit | No integration work for you |
| **Naming** | `<unit-id>/<anything>.jpg` | Only the folder name needs to be meaningful |

### Why it matters

Address matching catches a scammer who publishes your address. It does not
catch the ones who:

- post **your photos** under a **different address**,
- post your photos with **no address at all** (standard on Facebook),
- or deliberately misspell the address to dodge text matching.

Photos are the one thing the scammer **must** copy. A rental listing with no
photos does not rent. That makes your photographs the most reliable
fingerprint available — and on Facebook, combined with ZIP+4, the only thing
that can actually *prove* impersonation rather than merely suggest it.

**In one line:** ZIP+4 tells us *where* a listing claims to be. Photos tell us
*whose* listing it really is.

### How it works — plain English

We compute a "perceptual hash" for each of your photos: a short fingerprint
derived from the visual structure of the image, not the file itself.

The important property is that the fingerprint survives the things scammers do
to images — re-saving, resizing, cropping slightly, adjusting brightness,
adding a watermark, screenshotting. A pixel-for-pixel checksum breaks the
instant anything changes. A perceptual hash does not.

We then compare every scraped listing photo against your fingerprints. A close
match means your photo is being used in someone else's listing.

We store only the fingerprints — short strings, roughly 16 characters. We do
not need to retain your images after processing.

*Technical detail — algorithms, thresholds, schema and build order — is in
`layer3-image-hashing-spec.md`.*

### What we do NOT need

Stated plainly, because it should lower the barrier to saying yes:

- **No access** to your PMS, CRM, or any internal system
- **No tenant data**, lease data, or PII of any kind
- **No API integration**, no credentials, no engineering effort
- **No live feed** — a one-time drop per unit is enough

### Keeping photos current

When you re-shoot a unit, the old fingerprints go stale. Three options:

| Option | How it works | Effort on you |
|---|---|---|
| 1. Manual re-drop | You send a new folder when photos change | Low, but easy to forget |
| 2. Scheduled reminder | We ask quarterly | Low |
| 3. **We scrape your public listings** *(recommended)* | We pull photos from your own public site/portal automatically | **Zero after setup** |

Option 3 is what we'd suggest. It needs nothing from you beyond permission and
your public listing URLs, and it never goes stale.

### One thing we need in writing

Written permission to:

1. **Download and fingerprint** your marketing photographs, and
2. **Reference your copyright ownership** when reporting a fraudulent listing.

That second point is the practical one. Both Craigslist and Facebook remove
content far faster under a **copyright/DMCA complaint from the rights holder**
than under a generic "this is a scam" report. Scam reports get queued;
copyright claims get actioned. Having your ownership on record turns our
detection into actual takedowns.

---

## What you get back

- Alerts when a listing impersonating one of your units appears, typically
  within hours
- The listing URL, its photos, its claimed price, and the reason it was flagged
- A record of every detection for your own files
- Grounds for a copyright takedown, since the photos are demonstrably yours

---

## The question to put to your team

> *Can you send us (a) the ZIP+4 for each protected unit, and (b) a folder of
> original marketing photos per unit — plus written permission to fingerprint
> them and to cite your copyright when filing takedowns?*

Everything else is on us.

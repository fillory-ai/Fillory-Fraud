"""Craigslist detail-page enrichment.

The Apify Craigslist actor only returns search-result rows, which carry no
posting body and only occasionally a street address in the `location` field
(measured: 0/50 descriptions, 7/50 addresses). Craigslist detail pages,
however, serve the full body plus a structured `mapaddress` div and
lat/long data attributes, and they respond to a plain polite GET.

This module fetches those detail pages directly so the detector can match on
real address + body text instead of a neighbourhood name.
"""

import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Politeness settings. Craigslist tolerated 12/12 sequential requests at this
# delay in testing; keep concurrency at 1 and do not lower the delay.
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20
MAX_DETAIL_FETCHES = 60

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BODY_RE = re.compile(r'id="postingbody">(.*?)</section>', re.S)
_MAPADDR_RE = re.compile(r'<div class="mapaddress">(.*?)</div>', re.S)
_LAT_RE = re.compile(r'data-latitude="([-0-9.]+)"')
_LON_RE = re.compile(r'data-longitude="([-0-9.]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_QR_PREFIX_RE = re.compile(r"^\s*QR Code Link to This Post\s*", re.I)


def _clean_html(fragment: str) -> str:
    text = _TAG_RE.sub("", fragment)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    text = text.replace("&#x2F;", "/").replace("&quot;", '"').replace("&#39;", "'")
    text = _QR_PREFIX_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_detail(html: str) -> dict:
    """Pull body text, street address and coordinates out of a posting page."""
    result: dict = {}

    body_match = _BODY_RE.search(html)
    if body_match:
        body = _clean_html(body_match.group(1))
        if body:
            result["description"] = body

    addr_match = _MAPADDR_RE.search(html)
    if addr_match:
        addr = _clean_html(addr_match.group(1))
        if addr:
            result["street_address"] = addr[:300]

    lat_match, lon_match = _LAT_RE.search(html), _LON_RE.search(html)
    if lat_match and lon_match:
        try:
            result["latitude"] = float(lat_match.group(1))
            result["longitude"] = float(lon_match.group(1))
        except ValueError:
            pass

    return result


async def enrich_craigslist_listings(listings: list[dict]) -> list[dict]:
    """Fetch each Craigslist posting page and merge in body/address/geo.

    Mutates and returns the same list. Failures are non-fatal: a listing that
    cannot be enriched is left exactly as the search scrape produced it, so a
    Craigslist block degrades the scan rather than breaking it.
    """
    targets = [
        listing for listing in listings
        if listing.get("source") == "craigslist" and listing.get("url")
    ][:MAX_DETAIL_FETCHES]

    if not targets:
        return listings

    logger.info("Enriching %s Craigslist listings from detail pages", len(targets))
    enriched_count = 0
    address_count = 0
    blocked = 0

    async with httpx.AsyncClient(
        headers=_HEADERS, follow_redirects=True, timeout=REQUEST_TIMEOUT_SECONDS
    ) as client:
        for index, listing in enumerate(targets):
            if index:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
            try:
                response = await client.get(listing["url"])
                if response.status_code == 429 or response.status_code == 403:
                    blocked += 1
                    logger.warning(
                        "Craigslist returned %s — backing off, stopping enrichment",
                        response.status_code,
                    )
                    break
                if response.status_code != 200:
                    continue

                detail = _parse_detail(response.text)
                if not detail:
                    continue

                # Never overwrite good scrape data with nothing.
                if detail.get("description"):
                    listing["description"] = detail["description"]
                if detail.get("street_address"):
                    listing["street_address"] = detail["street_address"]
                    address_count += 1
                if detail.get("latitude") is not None:
                    listing["latitude"] = detail["latitude"]
                    listing["longitude"] = detail["longitude"]
                listing["enriched"] = True
                enriched_count += 1
            except Exception as exc:  # noqa: BLE001 - enrichment must never break a scan
                logger.warning(
                    "Detail fetch failed for %s: %s", listing.get("url"), exc
                )

    logger.info(
        "Craigslist enrichment complete: %s/%s enriched, %s with street address%s",
        enriched_count,
        len(targets),
        address_count,
        " (stopped early: rate limited)" if blocked else "",
    )
    return listings

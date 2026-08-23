"""Apify scraping service for rental fraud detector."""

import logging
import re
from datetime import datetime, timezone

from apify_client import ApifyClient

import config

logger = logging.getLogger(__name__)

CRAWLER_ACTOR_ID = "automation-lab/craigslist-scraper"
FACEBOOK_ACTOR_ID = "apify/facebook-marketplace-scraper"


def _parse_price(item: dict) -> float | None:
    """Extract a numeric price from an item.

    Handles priceNumeric (Craigslist actor), formatted strings like
    "$2,070", and listingPrice dicts (Facebook actor).
    """
    price = item.get("priceNumeric")
    if price is not None:
        try:
            return float(price)
        except (TypeError, ValueError):
            pass
    lp = item.get("listingPrice")
    if isinstance(lp, dict):
        try:
            return float(lp.get("amount") or lp.get("value") or 0) or None
        except (TypeError, ValueError):
            pass
    raw = str(item.get("price", "") or "")
    cleaned = re.sub(r"[^0-9.]", "", raw)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_posted_date(item: dict) -> str | None:
    for key in ("postedAt", "postedDate", "postDate", "date", "time", "timestamp"):
        val = item.get(key)
        if val:
            return str(val)
    return None


def _parse_images(item: dict) -> str:
    """Extract image URLs from any of the known field shapes."""
    urls: list[str] = []
    # Facebook: listingPhotos = [{image: {uri}}, ...]
    photos = item.get("listingPhotos")
    if isinstance(photos, list):
        for entry in photos:
            if isinstance(entry, dict):
                img = entry.get("image") or entry
                u = (img or {}).get("uri") or (img or {}).get("url") if isinstance(img, dict) else None
                if u:
                    urls.append(u)
    # Generic list shapes: imageUrls / images / photos
    for key in ("imageUrls", "images", "photos"):
        val = item.get(key)
        if isinstance(val, list):
            for entry in val:
                if isinstance(entry, str) and entry:
                    urls.append(entry)
                elif isinstance(entry, dict):
                    u = entry.get("uri") or entry.get("url") or entry.get("src")
                    if u:
                        urls.append(u)
    if not urls:
        # Facebook: primaryListingPhoto = {photo_image_url: ...}
        single = item.get("primaryListingPhoto") or item.get("imageUrl")
        if isinstance(single, dict):
            single = single.get("photo_image_url") or single.get("uri") or single.get("url")
        if isinstance(single, str) and single:
            urls.append(single)
    # Dedupe while preserving order, cap at a sane number
    seen: set[str] = set()
    result = [u for u in urls if not (u in seen or seen.add(u))]
    return ",".join(result[:10])


def _parse_location(item: dict) -> str:
    """Best-effort location string from flat or nested shapes."""
    # Facebook: location = {latitude, longitude, reverse_geocode_detailed: {city, state, postal_code}}
    loc = item.get("location")
    if isinstance(loc, dict):
        rg = loc.get("reverse_geocode_detailed") or {}
        if rg:
            parts = [rg.get("city"), rg.get("state"), rg.get("postal_code")]
            return ", ".join(str(p) for p in parts if p)
        parts = [loc.get("city"), loc.get("region"), loc.get("state")]
        return ", ".join(str(p) for p in parts if p) or ""
    if isinstance(loc, str) and loc:
        return loc
    if item.get("city"):
        return str(item["city"])
    return str(item.get("area", "") or "")


def _parse_description(item: dict) -> str:
    desc = item.get("description")
    if isinstance(desc, dict):
        return str(desc.get("text", "") or "")
    return str(desc or item.get("body", "") or "")


def _build_listing(source: str, item: dict) -> dict:
    """Normalize a raw Apify dataset item into our listing schema."""
    external_id = (
        item.get("listingId")
        or item.get("externalId")
        or item.get("id")
        or item.get("itemUrl")
        or item.get("url")
        or ""
    )

    return {
        "source": source,
        "external_id": str(external_id),
        "title": str(
            item.get("listingTitle")
            or item.get("title")
            or item.get("marketplace_listing_title")
            or ""
        ),
        "price": _parse_price(item),
        "location": _parse_location(item),
        "description": _parse_description(item),
        "url": str(item.get("itemUrl") or item.get("url") or item.get("listing_url") or ""),
        "image_urls": _parse_images(item),
        "posted_date": _parse_posted_date(item),
    }


async def search_craigslist(city: str, state: str) -> list[dict]:
    """Scrape Craigslist rental listings via Apify."""
    if not config.APIFY_API_KEY:
        logger.warning("APIFY_API_KEY not set; skipping Craigslist scrape")
        return []

    client = ApifyClient(config.APIFY_API_KEY)

    try:
        # Craigslist subdomains are lowercase city names without spaces
        # (e.g. "portland", "newyork", "losangeles").
        city_slug = city.lower().replace(" ", "")
        run_input = {
            "city": city_slug,
            "category": "housing",
            "searchQueries": [],  # empty = browse all housing listings
            "maxResults": 50,
        }
        run = client.actor(CRAWLER_ACTOR_ID).call(run_input=run_input)
        if not run or not run.default_dataset_id:
            logger.warning("Craigslist Apify run returned no dataset")
            return []

        items = client.dataset(run.default_dataset_id).list_items().items
        listings = [_build_listing("craigslist", item) for item in items]
        logger.info("Craigslist scrape returned %s listings", len(listings))
        return listings
    except Exception:
        logger.exception("Craigslist scrape failed")
        return []


async def search_facebook_marketplace(city: str, state: str) -> list[dict]:
    """Scrape Facebook Marketplace rental listings via Apify."""
    if not config.APIFY_API_KEY:
        logger.warning("APIFY_API_KEY not set; skipping Facebook Marketplace scrape")
        return []

    client = ApifyClient(config.APIFY_API_KEY)

    try:
        city_slug = city.lower().replace(" ", "")
        run_input = {
            "startUrls": [
                {
                    "url": (
                        f"https://www.facebook.com/marketplace/{city_slug}/search/"
                        "?query=apartment%20rental&exact=false"
                    )
                },
                {
                    "url": (
                        f"https://www.facebook.com/marketplace/{city_slug}/search/"
                        "?query=house%20for%20rent&exact=false"
                    )
                },
            ],
            "resultsLimit": 50,
            "includeListingDetails": True,
        }
        run = client.actor(FACEBOOK_ACTOR_ID).call(run_input=run_input)
        if not run or not run.default_dataset_id:
            logger.warning("Facebook Marketplace Apify run returned no dataset")
            return []

        items = client.dataset(run.default_dataset_id).list_items().items
        listings = [_build_listing("facebook_marketplace", item) for item in items]
        logger.info("Facebook Marketplace scrape returned %s listings", len(listings))
        return listings
    except Exception:
        logger.exception("Facebook Marketplace scrape failed")
        return []

"""Geocoding for registered properties.

Craigslist detail pages give us lat/long for nearly every listing. To compare
against, each registered property needs coordinates too. We geocode once and
cache the result on the property row — addresses don't move, so this runs at
most once per property.

Uses OpenStreetMap Nominatim: free, no API key. Its usage policy requires an
identifying User-Agent and a maximum of 1 request/second, both respected here.
"""

import logging
import math
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "fillory-fraud-detector/1.0 (rental listing monitoring)"}
REQUEST_DELAY_SECONDS = 1.1

# Two listings within this distance are treated as the same building.
# ~150m covers GPS jitter and Craigslist's habit of pinning to the block
# centroid, without bleeding into neighbouring buildings.
GEO_MATCH_RADIUS_METRES = 150.0

# Unit/apartment designators defeat Nominatim: "4411 NE Killingsworth St Unit
# 107, Portland, OR" resolves to nothing, while the same address without
# "Unit 107" resolves fine. Stripped on a retry pass.
_UNIT_RE = re.compile(
    r"[,\s]+(?:unit|apt\.?|apartment|ste\.?|suite|#)\s*[\w-]+",
    re.I,
)


def _strip_unit(address: str) -> str:
    return _UNIT_RE.sub("", address).strip(" ,")


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def geocode_address(query: str) -> tuple[float, float] | None:
    """Resolve a free-text address to (latitude, longitude), or None."""
    if not query.strip():
        return None
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers=_HEADERS,
            timeout=20,
        )
        if response.status_code != 200:
            logger.warning("Geocoding returned HTTP %s for %r", response.status_code, query)
            return None
        results = response.json()
        if not results:
            logger.info("Geocoding found no match for %r", query)
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        logger.exception("Geocoding failed for %r", query)
        return None


def geocode_pending_properties() -> int:
    """Geocode any registered property that has no coordinates yet.

    Returns the number of properties successfully geocoded. Safe to call on
    every scan: already-geocoded properties are skipped.
    """
    import time

    from database import SessionLocal
    from models import Property

    session = SessionLocal()
    geocoded = 0
    try:
        pending = session.query(Property).filter(Property.latitude.is_(None)).all()
        if not pending:
            return 0

        logger.info("Geocoding %s property/properties", len(pending))
        for index, prop in enumerate(pending):
            if index:
                time.sleep(REQUEST_DELAY_SECONDS)
            parts = [prop.address, prop.city, prop.state, prop.zip_code or ""]
            query = ", ".join(p for p in parts if p)
            coords = geocode_address(query)
            if not coords:
                # Retry without the unit/apartment designator.
                fallback_parts = [
                    _strip_unit(prop.address), prop.city, prop.state, prop.zip_code or ""
                ]
                fallback = ", ".join(p for p in fallback_parts if p)
                if fallback != query:
                    time.sleep(REQUEST_DELAY_SECONDS)
                    coords = geocode_address(fallback)
                    if coords:
                        query = fallback
            if not coords:
                continue
            prop.latitude, prop.longitude = coords
            prop.geocoded_at = datetime.now(timezone.utc)
            geocoded += 1
            logger.info("Geocoded %r -> %.6f, %.6f", query, coords[0], coords[1])
        session.commit()
    finally:
        session.close()
    return geocoded

"""Scan pipeline: scrape → identify → analyse → open cases → alert.

Extracted from routes.py in M1 so the scheduler can drive it without importing
the web layer.

The central change from v0 is that a *listing* is now a durable thing rather
than a row per sighting, and an *alert* belongs to a case rather than to a
detection. v0 re-inserted and re-alerted on every scan, which was tolerable
when a human clicked "Scan" once an hour and fatal the moment scanning became
automatic.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from database import SessionLocal
from models import (
    Property,
    ScrapedListing,
    ScrapeStatus,
    Alert,
    ScanLog,
    Case,
    CaseStatus,
)
from scraper import search_craigslist, search_facebook_marketplace
from craigslist_detail import enrich_craigslist_listings
from geocode import geocode_pending_properties
from detector import analyze_listing as analyze_listing_ai
from notifier import send_fraud_alert
from config import (
    ALERT_PHONE_NUMBER,
    SCRAPE_CITY,
    SCRAPE_STATE,
    OBSERVE_MODE,
    ALERT_COOLDOWN_HOURS,
    MAX_ALERTS_PER_DAY,
)

logger = logging.getLogger(__name__)

# A verdict is only acted on above this confidence.
FRAUD_CONFIDENCE_THRESHOLD = 0.7


def _now():
    return datetime.now(timezone.utc)


# ── Listing identity ────────────────────────────────────────────────────────

def content_fingerprint(listing_data: dict) -> str:
    """Hash of the fields that could change a verdict.

    Deliberately excludes anything that drifts without meaning (scrape time,
    ordering, our own enrichment flags). If this is unchanged on a re-sighting
    the previous analysis is still valid, so we skip the AI call — which is
    what makes 4-hourly scanning affordable.
    """
    parts = [
        str(listing_data.get("title") or ""),
        str(listing_data.get("price") or ""),
        str(listing_data.get("description") or ""),
        str(listing_data.get("location") or ""),
        str(listing_data.get("street_address") or ""),
        str(listing_data.get("image_urls") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _upsert_listing(session, listing_data: dict) -> tuple[uuid.UUID, bool, bool]:
    """Insert a listing, or update the existing row for the same posting.

    Returns (listing_id, is_new, content_changed).

    Identity is (source, external_id). Listings without an external_id —
    manual pastes — are always inserted, since there is nothing to match on.
    """
    fingerprint = content_fingerprint(listing_data)
    external_id = listing_data.get("external_id")
    now = _now()

    existing = None
    if external_id:
        existing = (
            session.query(ScrapedListing)
            .filter_by(source=listing_data["source"], external_id=external_id)
            .first()
        )

    if existing is not None:
        changed = existing.content_fingerprint != fingerprint
        existing.last_seen_at = now
        existing.times_seen = (existing.times_seen or 1) + 1
        # A listing we see again is not delisted, even if we thought it was.
        existing.delisted_at = None
        if changed:
            existing.title = listing_data.get("title", existing.title)
            existing.price = listing_data.get("price")
            existing.description = listing_data.get("description")
            existing.location = listing_data.get("location")
            existing.image_urls = listing_data.get("image_urls")
            existing.content_fingerprint = fingerprint
        # Enrichment can succeed on a later pass after failing on the first.
        if listing_data.get("enriched") and not existing.enriched:
            existing.street_address = listing_data.get("street_address")
            existing.latitude = listing_data.get("latitude")
            existing.longitude = listing_data.get("longitude")
            existing.enriched = True
        session.commit()
        return existing.id, False, changed

    listing = ScrapedListing(
        source=listing_data["source"],
        external_id=external_id,
        title=listing_data.get("title", "Untitled"),
        price=listing_data.get("price"),
        location=listing_data.get("location"),
        description=listing_data.get("description"),
        url=listing_data.get("url", ""),
        image_urls=listing_data.get("image_urls"),
        street_address=listing_data.get("street_address"),
        latitude=listing_data.get("latitude"),
        longitude=listing_data.get("longitude"),
        enriched=bool(listing_data.get("enriched")),
        fraud_status=ScrapeStatus.UNKNOWN,
        content_fingerprint=fingerprint,
        first_seen_at=now,
        last_seen_at=now,
        times_seen=1,
    )
    session.add(listing)
    session.commit()
    return listing.id, True, True


# ── Alert policy ────────────────────────────────────────────────────────────

def _alerts_in_last_day(session) -> int:
    cutoff = _now() - timedelta(hours=24)
    return (
        session.query(func.count(Alert.id))
        .filter(Alert.created_at >= cutoff, Alert.status == "sent")
        .scalar()
        or 0
    )


def _record_alert(session, case: Case, listing_data: dict, listing_id, property_name: str, kind: str) -> dict:
    """Decide whether to actually send, then record the outcome either way.

    Every decision produces an Alert row. A suppressed alert that leaves no
    trace is indistinguishable from a detection failure when someone later asks
    "why didn't I hear about this one?".
    """
    now = _now()

    if OBSERVE_MODE:
        status, error = "observed", "Observe mode: recorded, not sent."
    elif _alerts_in_last_day(session) >= MAX_ALERTS_PER_DAY:
        status, error = "suppressed_rate_limit", (
            f"Daily cap of {MAX_ALERTS_PER_DAY} alerts reached. "
            "Case is open and visible in the dashboard."
        )
    else:
        outcome = send_fraud_alert(listing_data, property_name)
        status = outcome.get("status", "failed")
        error = outcome.get("error_message")

    alert = Alert(
        listing_id=listing_id,
        property_id=case.property_id,
        case_id=case.id,
        alert_type="sms",
        recipient=ALERT_PHONE_NUMBER,
        message=(
            f"{'New' if kind == 'opened' else 'Updated'} case: {property_name} "
            f"impersonated on {listing_data.get('source', 'unknown')}"
        ),
        status=status,
        sent_at=now if status == "sent" else None,
        error_message=error,
    )
    session.add(alert)

    if status == "sent":
        case.last_alert_at = now
        case.alert_count = (case.alert_count or 0) + 1
    session.commit()

    return {"status": status, "sent": status == "sent"}


def _open_or_update_case(
    session, listing_id, property_id, result: dict, content_changed: bool
) -> tuple[Case, str]:
    """Return (case, action) where action is opened / changed / quiet.

    This is the dedup boundary: one case per (listing, property), alerted once
    on opening and thereafter only when the listing materially changes and the
    cooldown has elapsed.
    """
    case = (
        session.query(Case)
        .filter_by(listing_id=listing_id, property_id=property_id)
        .first()
    )

    if case is None:
        case = Case(
            listing_id=listing_id,
            property_id=property_id,
            status=CaseStatus.OPEN,
            confidence=result.get("confidence"),
            reason=result.get("reason"),
            match_signal=result.get("match_signal"),
        )
        session.add(case)
        session.commit()
        return case, "opened"

    case.confidence = result.get("confidence")
    case.reason = result.get("reason")
    case.updated_at = _now()

    # A case the user has already dealt with never re-alerts.
    if case.status in (CaseStatus.DISMISSED, CaseStatus.RESOLVED, CaseStatus.DISPUTED):
        session.commit()
        return case, "quiet"

    if not content_changed:
        session.commit()
        return case, "quiet"

    entry = f"{_now().isoformat()} listing content changed"
    case.change_log = f"{case.change_log}\n{entry}" if case.change_log else entry

    cooled = (
        case.last_alert_at is None
        or (_now() - case.last_alert_at) >= timedelta(hours=ALERT_COOLDOWN_HOURS)
    )
    session.commit()
    return case, ("changed" if cooled else "quiet")


# ── Per-listing processing ──────────────────────────────────────────────────

async def process_listing(listing_data: dict, property_dicts: list[dict]) -> dict:
    """Store/refresh one listing, analyse it if needed, and manage its case."""
    session = SessionLocal()
    try:
        listing_id, is_new, content_changed = _upsert_listing(session, listing_data)

        # Nothing about this listing changed and we already have a verdict:
        # skip the AI call entirely.
        if not is_new and not content_changed:
            existing = session.query(ScrapedListing).filter_by(id=listing_id).first()
            if existing is not None and existing.fraud_reason:
                return {
                    "listing_id": str(listing_id),
                    "is_new": False,
                    "reanalyzed": False,
                    "fraud_status": existing.fraud_status.value if existing.fraud_status else "unknown",
                    "confidence": existing.fraud_confidence,
                    "reason": existing.fraud_reason,
                    "matched_property_id": str(existing.matched_property_id) if existing.matched_property_id else None,
                    "case_action": "quiet",
                    "alert_status": None,
                    "alert_sent": False,
                }
    finally:
        session.close()

    try:
        result = await analyze_listing_ai(listing_data, property_dicts)
    except Exception as e:
        logger.exception("AI analysis failed for listing %s", listing_id)
        result = {
            "fraud_status": "unknown",
            "confidence": 0.0,
            "reason": f"AI analysis error: {e}",
            "matched_property_id": None,
            "match_signal": None,
        }

    session = SessionLocal()
    case_action = "none"
    alert_status = None
    alert_sent = False
    try:
        db_listing = session.query(ScrapedListing).filter_by(id=listing_id).first()
        if db_listing:
            db_listing.fraud_status = ScrapeStatus(result["fraud_status"])
            db_listing.fraud_confidence = result["confidence"]
            db_listing.fraud_reason = result["reason"]
            if result.get("matched_property_id"):
                db_listing.matched_property_id = uuid.UUID(result["matched_property_id"])
            session.commit()

        actionable = (
            result["fraud_status"] == "fraud"
            and result["confidence"] >= FRAUD_CONFIDENCE_THRESHOLD
            and result.get("matched_property_id")
        )

        if actionable:
            property_id = uuid.UUID(result["matched_property_id"])
            case, case_action = _open_or_update_case(
                session, listing_id, property_id, result, content_changed
            )
            if case_action in ("opened", "changed"):
                prop = session.query(Property).filter_by(id=property_id).first()
                outcome = _record_alert(
                    session,
                    case,
                    listing_data,
                    listing_id,
                    prop.name if prop else "Unknown property",
                    case_action,
                )
                alert_status = outcome["status"]
                alert_sent = outcome["sent"]
                if alert_status == "sent":
                    db_listing = session.query(ScrapedListing).filter_by(id=listing_id).first()
                    if db_listing:
                        db_listing.alerted_at = _now()
                        session.commit()
    finally:
        session.close()

    return {
        "listing_id": str(listing_id),
        "is_new": is_new,
        "reanalyzed": True,
        "fraud_status": result["fraud_status"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "matched_property_id": result.get("matched_property_id"),
        "match_signal": result.get("match_signal"),
        "case_action": case_action,
        "alert_status": alert_status,
        "alert_sent": alert_sent,
    }


# ── Delisting ───────────────────────────────────────────────────────────────

def _mark_delisted(session, source: str, seen_external_ids: set[str], scan_started) -> int:
    """Flag listings that a successful scan of this source no longer returns.

    Only applied to listings we saw within the last 7 days: older ones fell out
    of the scraper's result window long ago and their absence means nothing.
    Guarded by the caller so this never runs after a failed scrape — otherwise
    a scraper outage would look like the entire market being taken down.
    """
    if not seen_external_ids:
        return 0
    cutoff = _now() - timedelta(days=7)
    stale = (
        session.query(ScrapedListing)
        .filter(
            ScrapedListing.source == source,
            ScrapedListing.external_id.isnot(None),
            ScrapedListing.external_id.notin_(seen_external_ids),
            ScrapedListing.last_seen_at >= cutoff,
            ScrapedListing.last_seen_at < scan_started,
            ScrapedListing.delisted_at.is_(None),
        )
        .all()
    )
    for listing in stale:
        listing.delisted_at = _now()
    if stale:
        session.commit()
    return len(stale)


# ── Full scan ───────────────────────────────────────────────────────────────

async def run_scan(source: str | None = None, trigger: str = "manual") -> dict:
    """Run the full scan pipeline: scrape → analyse → case → alert."""
    city = SCRAPE_CITY
    state = SCRAPE_STATE
    scan_started = _now()

    session = SessionLocal()
    scan_log = ScanLog(source=source or "all", status="running", trigger=trigger)
    session.add(scan_log)
    session.commit()
    scan_id = scan_log.id
    session.close()

    all_listings: list[dict] = []
    per_source: dict[str, list[dict]] = {}
    source_ok: dict[str, bool] = {}
    enrichment_rate = None
    errors: list[str] = []

    if source in (None, "all", "craigslist"):
        try:
            cl_listings = await search_craigslist(city, state)
            try:
                await enrich_craigslist_listings(cl_listings)
            except Exception:
                logger.exception("Craigslist enrichment error (continuing unenriched)")
            if cl_listings:
                enriched = sum(1 for l in cl_listings if l.get("enriched"))
                enrichment_rate = enriched / len(cl_listings)
            per_source["craigslist"] = cl_listings
            source_ok["craigslist"] = True
            all_listings.extend(cl_listings)
        except Exception as e:
            logger.exception("Craigslist scrape error")
            source_ok["craigslist"] = False
            errors.append(f"craigslist: {e}")

    if source in (None, "all", "facebook_marketplace"):
        try:
            fb_listings = await search_facebook_marketplace(city, state)
            per_source["facebook_marketplace"] = fb_listings
            source_ok["facebook_marketplace"] = True
            all_listings.extend(fb_listings)
        except Exception as e:
            logger.exception("Facebook Marketplace scrape error")
            source_ok["facebook_marketplace"] = False
            errors.append(f"facebook_marketplace: {e}")

    logger.info("Total listings found: %s", len(all_listings))

    try:
        geocode_pending_properties()
    except Exception:
        logger.exception("Property geocoding error (continuing without geo match)")

    session = SessionLocal()
    property_dicts = [p.to_dict() for p in session.query(Property).all()]
    session.close()

    new_count = updated_count = fraud_count = alert_count = cases_opened = 0

    for listing_data in all_listings:
        result = await process_listing(listing_data, property_dicts)
        if result["is_new"]:
            new_count += 1
        else:
            updated_count += 1
        if result["fraud_status"] == "fraud" and (result["confidence"] or 0) >= FRAUD_CONFIDENCE_THRESHOLD:
            fraud_count += 1
        if result.get("case_action") == "opened":
            cases_opened += 1
        if result["alert_sent"]:
            alert_count += 1

    # Delisting is only meaningful when the scrape itself succeeded.
    delisted = 0
    session = SessionLocal()
    try:
        for src, listings in per_source.items():
            if not source_ok.get(src):
                continue
            seen = {l.get("external_id") for l in listings if l.get("external_id")}
            delisted += _mark_delisted(session, src, seen, scan_started)
    finally:
        session.close()

    # A scan where every requested source failed is a failure, not an empty
    # market. Recording it as "completed" would hide an outage.
    all_failed = bool(source_ok) and not any(source_ok.values())
    status = "failed" if all_failed else "completed"

    session = SessionLocal()
    scan = session.query(ScanLog).filter_by(id=scan_id).first()
    if scan:
        scan.listings_found = len(all_listings)
        scan.listings_new = new_count
        scan.listings_updated = updated_count
        scan.cases_opened = cases_opened
        scan.enrichment_rate = enrichment_rate
        scan.fraud_found = fraud_count
        scan.alerts_sent = alert_count
        scan.status = status
        scan.error_message = "; ".join(errors) if errors else None
        scan.completed_at = _now()
        session.commit()
    session.close()

    return {
        "scan_id": str(scan_id),
        "status": status,
        "trigger": trigger,
        "listings_found": len(all_listings),
        "listings_new": new_count,
        "listings_updated": updated_count,
        "cases_opened": cases_opened,
        "delisted": delisted,
        "enrichment_rate": enrichment_rate,
        "fraud_found": fraud_count,
        "alerts_sent": alert_count,
        "errors": errors,
        "source": source or "all",
    }

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
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from database import db_session
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
    DELIST_MIN_COVERAGE,
    DELIST_MISS_THRESHOLD,
    DELIST_COVERAGE_WINDOW,
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


def _claim_listing(session, listing_data: dict, fingerprint: str, now) -> uuid.UUID | None:
    """Try to insert a new listing row, returning its id, or None on conflict.

    Separated out and driven by INSERT ... ON CONFLICT DO NOTHING rather than
    trusting a preceding SELECT: a scheduled scan and a manual scan can overlap
    on the same posting, and losing that race used to raise IntegrityError
    partway through a scan.
    """
    stmt = (
        pg_insert(ScrapedListing.__table__)
        .values(
            id=uuid.uuid4(),
            source=listing_data["source"],
            external_id=listing_data.get("external_id"),
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
            consecutive_misses=0,
            created_at=now,
            scraped_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["source", "external_id"],
            # The unique index is partial; Postgres can only infer it as the
            # conflict arbiter if the predicate is restated here.
            index_where=text("external_id IS NOT NULL"),
        )
        .returning(ScrapedListing.__table__.c.id)
    )
    inserted = session.execute(stmt).scalar()
    session.commit()
    return inserted


def _upsert_listing(session, listing_data: dict) -> tuple[uuid.UUID, bool, bool]:
    """Insert a listing, or update the existing row for the same posting.

    Returns (listing_id, is_new, content_changed).

    Identity is (source, external_id). Listings without an external_id —
    manual pastes — are always inserted, since there is nothing to match on.

    The insert races the unique index rather than trusting the preceding
    SELECT: a scheduled scan and a manual scan can overlap, and losing that
    race used to raise IntegrityError mid-scan.
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

    if existing is None and external_id:
        inserted = _claim_listing(session, listing_data, fingerprint, now)
        if inserted is not None:
            return inserted, True, True
        # Lost the race: another worker inserted it between our SELECT and our
        # INSERT. Fall through to the update path.
        existing = (
            session.query(ScrapedListing)
            .filter_by(source=listing_data["source"], external_id=external_id)
            .first()
        )

    if existing is not None:
        changed = existing.content_fingerprint != fingerprint
        existing.last_seen_at = now
        existing.times_seen = (existing.times_seen or 1) + 1
        # A listing we see again is not delisted, even if we thought it was,
        # and its miss streak restarts.
        existing.delisted_at = None
        existing.consecutive_misses = 0
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

    # No external_id: a manual paste. Nothing to collide with.
    listing = ScrapedListing(
        source=listing_data["source"],
        external_id=None,
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

# An alert that consumed the day's budget, whether or not a text left the
# building. Observe mode has to count, otherwise the volume you watch in
# observe mode is not the volume you would get live — which is the only
# question observe mode exists to answer.
BUDGETED_ALERT_STATUSES = ("sent", "observed")
# Statuses that start the per-case cooldown clock. A genuine send *failure*
# deliberately does not, so the next material change retries.
COOLDOWN_STATUSES = ("sent", "observed", "suppressed_rate_limit")


def _alerts_in_last_day(session) -> int:
    cutoff = _now() - timedelta(hours=24)
    return (
        session.query(func.count(Alert.id))
        .filter(Alert.created_at >= cutoff, Alert.status.in_(BUDGETED_ALERT_STATUSES))
        .scalar()
        or 0
    )


def _record_alert(session, case: Case, listing_data: dict, listing_id, property_name: str, kind: str) -> dict:
    """Decide whether to actually send, then record the outcome either way.

    Every decision produces an Alert row. A suppressed alert that leaves no
    trace is indistinguishable from a detection failure when someone later asks
    "why didn't I hear about this one?".

    The rate cap is evaluated *before* the observe-mode branch so both modes
    make the same decisions in the same order.
    """
    now = _now()

    if _alerts_in_last_day(session) >= MAX_ALERTS_PER_DAY:
        status, error = "suppressed_rate_limit", (
            f"Daily cap of {MAX_ALERTS_PER_DAY} alerts reached. "
            "Case is open and visible in the dashboard."
        )
    elif OBSERVE_MODE:
        status, error = "observed", "Observe mode: recorded, not sent."
    else:
        # A notifier blowing up must not abort the scan or leave a case open
        # with no audit trail; it is recorded as a failed alert like any other.
        try:
            outcome = send_fraud_alert(listing_data, property_name)
            status = outcome.get("status", "failed")
            error = outcome.get("error_message")
        except Exception as e:
            logger.exception("Notifier raised for case %s", case.id)
            status, error = "failed", f"Notifier error: {e}"

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

    if status in COOLDOWN_STATUSES:
        case.last_alert_at = now
    if status in BUDGETED_ALERT_STATUSES:
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
        try:
            # flush, not commit: the case and the alert row that follows it are
            # one unit of work. A case visible with no alert record is exactly
            # the "why didn't I hear about this?" gap the audit trail exists to
            # close.
            session.flush()
            return case, "opened"
        except IntegrityError:
            # Another worker opened the same case between our SELECT and our
            # INSERT. Theirs is as good as ours; adopt it and fall through to
            # the update path rather than crashing the scan.
            session.rollback()
            case = (
                session.query(Case)
                .filter_by(listing_id=listing_id, property_id=property_id)
                .first()
            )
            if case is None:
                raise

    case.confidence = result.get("confidence")
    case.reason = result.get("reason")
    case.updated_at = _now()

    # A case the user has already dealt with never re-alerts.
    if case.status in (CaseStatus.DISMISSED, CaseStatus.RESOLVED, CaseStatus.DISPUTED):
        session.flush()
        return case, "quiet"

    if not content_changed:
        session.flush()
        return case, "quiet"

    entry = f"{_now().isoformat()} listing content changed"
    case.change_log = f"{case.change_log}\n{entry}" if case.change_log else entry

    cooled = (
        case.last_alert_at is None
        or (_now() - case.last_alert_at) >= timedelta(hours=ALERT_COOLDOWN_HOURS)
    )
    session.flush()
    return case, ("changed" if cooled else "quiet")


# ── Per-listing processing ──────────────────────────────────────────────────

async def process_listing(listing_data: dict, property_dicts: list[dict]) -> dict:
    """Store/refresh one listing, analyse it if needed, and manage its case."""
    with db_session() as session:
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

    case_action = "none"
    alert_status = None
    alert_sent = False
    with db_session() as session:
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
            else:
                # No alert followed, so nothing has committed the case
                # bookkeeping that _open_or_update_case only flushed.
                session.commit()

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

def _recent_source_average(session, source: str, exclude_scan_id=None) -> float | None:
    """Mean row count this source returned over recent completed scans.

    Returns None when there is no history to compare against — in which case
    the caller declines to delist rather than guessing.
    """
    rows = (
        session.query(ScanLog.source_counts)
        .filter(ScanLog.status == "completed", ScanLog.source_counts.isnot(None))
        .order_by(ScanLog.started_at.desc())
        .limit(DELIST_COVERAGE_WINDOW + 1)
        .all()
    )
    counts = []
    for (raw,) in rows:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if source in parsed:
            counts.append(parsed[source])
    counts = counts[:DELIST_COVERAGE_WINDOW]
    if not counts:
        return None
    return sum(counts) / len(counts)


def _mark_delisted(
    session,
    source: str,
    seen_external_ids: set[str],
    scan_started,
    row_count: int | None = None,
) -> int:
    """Flag listings that repeated complete scans of this source no longer return.

    Two guards, because marking a live scam "delisted" removes it from
    monitoring silently:

    1. *Coverage* — if this scan returned materially fewer rows than this
       source's recent average, the scrape was probably truncated (Apify
       returning 20 rows instead of 50 with no exception is a real failure
       mode), so nothing is delisted at all.
    2. *Persistence* — a listing missing from a qualifying scan increments a
       miss counter; only once it crosses DELIST_MISS_THRESHOLD consecutive
       misses is it actually marked delisted. Any re-sighting resets it.

    Only applied to listings we saw within the last 7 days: older ones fell out
    of the scraper's result window long ago and their absence means nothing.
    Guarded by the caller so this never runs after a failed scrape.
    """
    if not seen_external_ids:
        return 0

    if row_count is None:
        row_count = len(seen_external_ids)
    average = _recent_source_average(session, source)
    if average is None:
        logger.info("Delisting skipped for %s: no scan history to compare against", source)
        return 0
    if average > 0 and row_count < DELIST_MIN_COVERAGE * average:
        logger.warning(
            "Delisting skipped for %s: %s rows is below %.0f%% of the recent average %.1f "
            "— treating this as a truncated scrape, not an emptied market",
            source, row_count, DELIST_MIN_COVERAGE * 100, average,
        )
        return 0

    cutoff = _now() - timedelta(days=7)
    missing = (
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
    newly_delisted = 0
    for listing in missing:
        listing.consecutive_misses = (listing.consecutive_misses or 0) + 1
        if listing.consecutive_misses >= DELIST_MISS_THRESHOLD:
            listing.delisted_at = _now()
            newly_delisted += 1
    if missing:
        session.commit()
    return newly_delisted


# ── Full scan ───────────────────────────────────────────────────────────────

async def run_scan(source: str | None = None, trigger: str = "manual") -> dict:
    """Run the full scan pipeline: scrape → analyse → case → alert."""
    city = SCRAPE_CITY
    state = SCRAPE_STATE
    scan_started = _now()

    with db_session() as session:
        scan_log = ScanLog(source=source or "all", status="running", trigger=trigger)
        session.add(scan_log)
        session.commit()
        scan_id = scan_log.id

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

    with db_session() as session:
        property_dicts = [p.to_dict() for p in session.query(Property).all()]

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

    # Delisting is only meaningful when the scrape itself succeeded, and the
    # coverage guard inside _mark_delisted needs the raw row count — a source
    # can return rows with no external_id, and those must not inflate coverage.
    delisted = 0
    with db_session() as session:
        for src, listings in per_source.items():
            if not source_ok.get(src):
                continue
            seen = {l.get("external_id") for l in listings if l.get("external_id")}
            delisted += _mark_delisted(session, src, seen, scan_started, row_count=len(listings))

    # A scan where every requested source failed is a failure, not an empty
    # market. Recording it as "completed" would hide an outage.
    all_failed = bool(source_ok) and not any(source_ok.values())
    status = "failed" if all_failed else "completed"

    with db_session() as session:
        scan = session.query(ScanLog).filter_by(id=scan_id).first()
        if scan:
            scan.listings_found = len(all_listings)
            scan.listings_new = new_count
            scan.listings_updated = updated_count
            scan.cases_opened = cases_opened
            scan.enrichment_rate = enrichment_rate
            # Feeds the next scan's delisting coverage guard.
            scan.source_counts = json.dumps({s: len(v) for s, v in per_source.items()})
            scan.fraud_found = fraud_count
            scan.alerts_sent = alert_count
            scan.status = status
            scan.error_message = "; ".join(errors) if errors else None
            scan.completed_at = _now()
            session.commit()

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

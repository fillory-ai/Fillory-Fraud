"""Database models for rental fraud detector."""
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Float,
    DateTime,
    Integer,
    Index,
    text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from database import Base

import enum


class ScrapeStatus(enum.Enum):
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNKNOWN = "unknown"


class Property(Base):
    """A property that the management firm owns and wants to protect."""
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    address = Column(String(500), nullable=False)
    city = Column(String(200), nullable=False)
    state = Column(String(100), nullable=False)
    zip_code = Column(String(20), nullable=True)
    # Full USPS ZIP+4 (e.g. "97218-1234"). A ZIP+4 resolves to roughly one
    # block face or one building, so an exact match is a far stronger signal
    # than the 5-digit ZIP. Facebook listings expose ZIP+4 ~80% of the time
    # but never a street address, making this the only usable geo hook there.
    zip_plus4 = Column(String(12), nullable=True)
    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Float, nullable=True)
    square_footage = Column(Integer, nullable=True)
    monthly_rent = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    image_urls = Column(Text, nullable=True)  # comma-separated URLs
    amenities = Column(Text, nullable=True)  # comma-separated
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    geocoded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "zip_plus4": self.zip_plus4,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "square_footage": self.square_footage,
            "monthly_rent": self.monthly_rent,
            "description": self.description,
            "image_urls": self.image_urls.split(",") if self.image_urls else [],
            "amenities": self.amenities.split(",") if self.amenities else [],
            "latitude": self.latitude,
            "longitude": self.longitude,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScrapedListing(Base):
    """A listing scraped from Craigslist or Facebook Marketplace."""
    __tablename__ = "scraped_listings"

    __table_args__ = (
        # One row per real posting. Partial because manually-pasted listings
        # have no external_id and must stay insertable.
        Index(
            "uq_listing_source_external",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_listing_last_seen", "last_seen_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False)  # "craigslist" or "facebook_marketplace"
    external_id = Column(String(500), nullable=True)
    title = Column(String(500), nullable=False)
    price = Column(Float, nullable=True)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    url = Column(String(1000), nullable=False)
    image_urls = Column(Text, nullable=True)
    posted_date = Column(DateTime(timezone=True), nullable=True)
    street_address = Column(String(300), nullable=True)  # from detail-page enrichment
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    enriched = Column(Boolean, default=False)  # detail page fetched?
    # ── Listing identity over time ────────────────────────────────────────
    # A listing is one real-world posting, not one row per sighting. Repeat
    # sightings update these instead of inserting a duplicate.
    first_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    times_seen = Column(Integer, default=1, nullable=False)
    # Set when a scan of its market no longer returns it — the closest thing we
    # get to "this listing was taken down", and the signal enforcement tracking
    # needs to prove a takedown worked.
    delisted_at = Column(DateTime(timezone=True), nullable=True)
    # How many consecutive *qualifying* scans of this source have come back
    # without this listing. A listing is only delisted once this crosses
    # DELIST_MISS_THRESHOLD, so one short page of results can't retire a live
    # scam. Reset to 0 on every re-sighting.
    consecutive_misses = Column(Integer, default=0, nullable=False)
    # Hash of the fields that would change our verdict. Unchanged fingerprint
    # on re-sighting means we can skip re-running the AI, which is the main
    # cost gate on scanning frequently.
    content_fingerprint = Column(String(64), nullable=True)
    fraud_status = Column(SAEnum(ScrapeStatus), default=ScrapeStatus.UNKNOWN)
    fraud_confidence = Column(Float, nullable=True)
    fraud_reason = Column(Text, nullable=True)
    matched_property_id = Column(UUID(as_uuid=True), nullable=True)
    alerted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    scraped_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self, full: bool = False):
        description = self.description
        if not full and description and len(description) > 300:
            description = description[:300] + "..."
        return {
            "id": str(self.id),
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "price": self.price,
            "location": self.location,
            "description": description,
            "url": self.url,
            "image_urls": self.image_urls.split(",") if self.image_urls else [],
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "street_address": self.street_address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "enriched": bool(self.enriched),
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "times_seen": self.times_seen or 1,
            "delisted_at": self.delisted_at.isoformat() if self.delisted_at else None,
            "fraud_status": self.fraud_status.value if self.fraud_status else "unknown",
            "fraud_confidence": self.fraud_confidence,
            "fraud_reason": self.fraud_reason,
            "matched_property_id": str(self.matched_property_id) if self.matched_property_id else None,
            "alerted_at": self.alerted_at.isoformat() if self.alerted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


class CaseStatus(enum.Enum):
    """Lifecycle of a suspected impersonation."""
    OPEN = "open"            # detected, not yet acted on
    ACKNOWLEDGED = "acknowledged"
    FILED = "filed"          # takedown notice sent (v1 M5)
    RESOLVED = "resolved"    # listing gone
    DISMISSED = "dismissed"  # false positive, or the firm's own listing
    DISPUTED = "disputed"    # counter-notice received; all automation stops


def _load_json(raw):
    """Parse a JSON text column, tolerating NULL and anything malformed.

    Evidence columns must never be able to break a list endpoint — a case that
    cannot be displayed is worse than one displayed without its snapshot.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# Why a case was closed. Kept separate from CaseStatus because the status says
# what happened to the case and the code says what it tells us about the
# detector. Only the first three are failures we should be tuning against.
RESOLUTION_CODES = {
    "false_positive_match":     "Not our property — the address match was wrong",
    "false_positive_authorized": "Our own or an authorised agent's advert",
    "false_positive_legitimate": "A genuine third-party listing, correctly matched",
    "confirmed_fraud":          "Confirmed impersonation",
    "listing_removed":          "Listing gone from the platform",
    "no_action":                "Reviewed, no action warranted",
}


class Case(Base):
    """A suspected impersonation of one property by one listing.

    The unit of *alerting*. v0 alerted per detection, which meant the same
    fraudulent listing paged the user on every scan — the defect that would
    make scheduled scanning unusable. A case is opened once, alerted once, and
    thereafter updated silently unless something material changes.
    """
    __tablename__ = "cases"

    __table_args__ = (
        # One case per (listing, property) pair. Re-detection finds this row
        # instead of creating another.
        Index("uq_case_listing_property", "listing_id", "property_id", unique=True),
        Index("ix_case_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), nullable=False)
    property_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(SAEnum(CaseStatus), default=CaseStatus.OPEN, nullable=False)
    confidence = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    # Which signal produced the match, for auditability.
    match_signal = Column(String(100), nullable=True)
    opened_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # Alert bookkeeping — the whole point of the table.
    last_alert_at = Column(DateTime(timezone=True), nullable=True)
    alert_count = Column(Integer, default=0, nullable=False)
    # Free-text log of what changed since opening (price moved, relisted, etc.)
    change_log = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # ── Evidence and model governance ───────────────────────────────────────
    # The listing exactly as it read when this case was opened, as JSON.
    #
    # scraped_listings rows are mutated in place on re-sighting, so without this
    # the input that produced the verdict is destroyed the first time the poster
    # edits their ad — which is precisely what someone who suspects they have
    # been spotted will do. Write-once: set at open, never updated.
    opening_evidence = Column(Text, nullable=True)
    # Which model answered, and a hash of the prompt that was sent. Without
    # these, a verdict from before a prompt edit and one from after are
    # indistinguishable, and no past decision can be reproduced.
    model_name = Column(String(100), nullable=True)
    prompt_version = Column(String(32), nullable=True)
    # Why a case was closed, distinct from the fact that it was. "dismissed"
    # alone conflates a bad match, our own customer's advert, and a legitimate
    # third party — three different bugs. This is the tuning feedback loop.
    resolution_code = Column(String(50), nullable=True)
    resolution_note = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "listing_id": str(self.listing_id),
            "property_id": str(self.property_id),
            "status": self.status.value if self.status else "open",
            "confidence": self.confidence,
            "reason": self.reason,
            "match_signal": self.match_signal,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_alert_at": self.last_alert_at.isoformat() if self.last_alert_at else None,
            "alert_count": self.alert_count or 0,
            "change_log": self.change_log,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "opening_evidence": _load_json(self.opening_evidence),
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "resolution_code": self.resolution_code,
            "resolution_note": self.resolution_note,
        }


class Alert(Base):
    """An alert sent to the user about a suspicious listing."""
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), nullable=False)
    property_id = Column(UUID(as_uuid=True), nullable=True)
    case_id = Column(UUID(as_uuid=True), nullable=True)
    alert_type = Column(String(50), nullable=False)  # "sms", "email"
    recipient = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="pending")  # "pending", "sent", "failed"
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # The listing as it read at the moment this alert fired. The Case keeps the
    # evidence at open; this keeps it at every subsequent alert, so a case that
    # re-alerts after a material change has a record of both versions.
    evidence_snapshot = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "listing_id": str(self.listing_id),
            "property_id": str(self.property_id) if self.property_id else None,
            "case_id": str(self.case_id) if self.case_id else None,
            "alert_type": self.alert_type,
            "recipient": self.recipient,
            "message": self.message[:200] + "..." if len(self.message) > 200 else self.message,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "evidence_snapshot": _load_json(self.evidence_snapshot),
        }


class ScanLog(Base):
    """Log of each scan run."""
    __tablename__ = "scan_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False)
    # "manual" (someone clicked Scan) or "scheduled" (the M1 scheduler).
    trigger = Column(String(20), default="manual", nullable=False)
    listings_found = Column(Integer, default=0)
    listings_new = Column(Integer, default=0)
    listings_updated = Column(Integer, default=0)
    cases_opened = Column(Integer, default=0)
    # Share of Craigslist rows whose detail page fetched successfully. A
    # sustained drop here means the scraper is being blocked — the failure mode
    # that would otherwise look like "no fraud found".
    enrichment_rate = Column(Float, nullable=True)
    # JSON map of source → row count for this scan. Feeds the delisting
    # coverage guard: we can only tell "the market is empty" from "the scraper
    # returned half a page" by comparing against recent scans.
    source_counts = Column(Text, nullable=True)
    fraud_found = Column(Integer, default=0)
    alerts_sent = Column(Integer, default=0)
    status = Column(String(50), default="running")  # "running", "completed", "failed"
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "source": self.source,
            "trigger": self.trigger or "manual",
            "listings_found": self.listings_found,
            "listings_new": self.listings_new or 0,
            "listings_updated": self.listings_updated or 0,
            "cases_opened": self.cases_opened or 0,
            "enrichment_rate": self.enrichment_rate,
            "fraud_found": self.fraud_found,
            "alerts_sent": self.alerts_sent,
            "status": self.status,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
"""Database models for rental fraud detector."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Boolean, Float, DateTime, Integer, Enum as SAEnum
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
            "fraud_status": self.fraud_status.value if self.fraud_status else "unknown",
            "fraud_confidence": self.fraud_confidence,
            "fraud_reason": self.fraud_reason,
            "matched_property_id": str(self.matched_property_id) if self.matched_property_id else None,
            "alerted_at": self.alerted_at.isoformat() if self.alerted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


class Alert(Base):
    """An alert sent to the user about a suspicious listing."""
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), nullable=False)
    property_id = Column(UUID(as_uuid=True), nullable=True)
    alert_type = Column(String(50), nullable=False)  # "sms", "email"
    recipient = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="pending")  # "pending", "sent", "failed"
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": str(self.id),
            "listing_id": str(self.listing_id),
            "property_id": str(self.property_id) if self.property_id else None,
            "alert_type": self.alert_type,
            "recipient": self.recipient,
            "message": self.message[:200] + "..." if len(self.message) > 200 else self.message,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScanLog(Base):
    """Log of each scan run."""
    __tablename__ = "scan_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False)
    listings_found = Column(Integer, default=0)
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
            "listings_found": self.listings_found,
            "fraud_found": self.fraud_found,
            "alerts_sent": self.alerts_sent,
            "status": self.status,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
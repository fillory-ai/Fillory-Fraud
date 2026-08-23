import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import init_db, SessionLocal
from models import (
    Property,
    ScrapedListing,
    ScrapeStatus,
    Alert,
    ScanLog,
)
from scraper import search_craigslist, search_facebook_marketplace
from craigslist_detail import enrich_craigslist_listings
from geocode import geocode_pending_properties
from detector import analyze_listing as analyze_listing_ai
from detector import parse_listing_text
from notifier import send_fraud_alert
from config import (
    APIFY_API_KEY,
    TWILIO_ENABLED,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    ALERT_PHONE_NUMBER,
    SCRAPE_CITY,
    SCRAPE_STATE,
)

logger = logging.getLogger(__name__)

# ─── Pydantic Schemas ───────────────────────────────────────────────────────

class PropertyCreate(BaseModel):
    name: str
    address: str
    city: str
    state: str
    zip_code: str | None = None
    zip_plus4: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    square_footage: int | None = None
    monthly_rent: float | None = None
    description: str | None = None
    image_urls: list[str] | None = None
    amenities: list[str] | None = None


class PropertyUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    zip_plus4: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    square_footage: int | None = None
    monthly_rent: float | None = None
    description: str | None = None
    image_urls: list[str] | None = None
    amenities: list[str] | None = None


class ConfigStatus(BaseModel):
    apify_configured: bool = False
    twilio_configured: bool = False
    twilio_enabled: bool = False
    gemini_configured: bool = False
    scrape_city: str = ""
    scrape_state: str = ""
    alert_phone: str = ""


# ─── Scanner Logic ──────────────────────────────────────────────────────────

async def _process_listing(listing_data: dict, property_dicts: list[dict]) -> dict:
    """Insert one listing, run AI fraud analysis, update its status, and
    record an alert when fraud is detected with high confidence.

    Returns a dict with the stored listing_id, the analysis result, and
    alert outcome (alert_status is None when no alert was attempted).
    """
    session = SessionLocal()
    listing = ScrapedListing(
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
    )
    session.add(listing)
    session.commit()
    listing_id = listing.id
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
        }

    session = SessionLocal()
    db_listing = session.query(ScrapedListing).filter_by(id=listing_id).first()
    if db_listing:
        db_listing.fraud_status = ScrapeStatus(result["fraud_status"])
        db_listing.fraud_confidence = result["confidence"]
        db_listing.fraud_reason = result["reason"]
        if result.get("matched_property_id"):
            db_listing.matched_property_id = uuid.UUID(result["matched_property_id"])
        session.commit()

    alert_status = None
    alert_sent = False
    if result["fraud_status"] == "fraud" and result["confidence"] >= 0.7:
        matched_name = "Unknown"
        if result.get("matched_property_id"):
            prop = session.query(Property).filter_by(
                id=uuid.UUID(result["matched_property_id"])
            ).first()
            if prop:
                matched_name = prop.name

        alert_result = send_fraud_alert(listing_data, matched_name)
        alert_status = alert_result.get("status", "failed")

        alert = Alert(
            listing_id=listing_id,
            property_id=uuid.UUID(result["matched_property_id"]) if result.get("matched_property_id") else None,
            alert_type="sms",
            recipient=ALERT_PHONE_NUMBER,
            message=f"Fraud alert for {matched_name} on {listing_data.get('source', 'Unknown')}",
            status=alert_result.get("status", "failed"),
            sent_at=datetime.now(timezone.utc) if alert_result.get("status") == "sent" else None,
            error_message=alert_result.get("error_message"),
        )
        session.add(alert)
        session.commit()
        alert_sent = alert_result.get("status") == "sent"

    session.close()

    return {
        "listing_id": str(listing_id),
        "fraud_status": result["fraud_status"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "matched_property_id": result.get("matched_property_id"),
        "alert_status": alert_status,
        "alert_sent": alert_sent,
    }


async def run_scan(source: str | None = None) -> dict:
    """Run the full scan pipeline: scrape -> analyze -> alert."""
    city = SCRAPE_CITY
    state = SCRAPE_STATE

    session = SessionLocal()
    scan_log = ScanLog(source=source or "all", status="running")
    session.add(scan_log)
    session.commit()
    scan_id = scan_log.id
    session.close()

    all_listings = []

    if source in (None, "all", "craigslist"):
        try:
            cl_listings = await search_craigslist(city, state)
            # The Apify actor returns search rows only: no posting body and
            # rarely a street address. Fetching each posting page directly
            # fills in body text, street address and coordinates.
            try:
                await enrich_craigslist_listings(cl_listings)
            except Exception:
                logger.exception("Craigslist enrichment error (continuing unenriched)")
            all_listings.extend(cl_listings)
        except Exception:
            logger.exception("Craigslist scrape error")

    if source in (None, "all", "facebook_marketplace"):
        try:
            fb_listings = await search_facebook_marketplace(city, state)
            all_listings.extend(fb_listings)
        except Exception:
            logger.exception("Facebook Marketplace scrape error")

    logger.info("Total listings found: %s", len(all_listings))

    # Properties need coordinates before geo matching can work. Cached on the
    # row, so this is a no-op after the first scan.
    try:
        geocode_pending_properties()
    except Exception:
        logger.exception("Property geocoding error (continuing without geo match)")

    session = SessionLocal()
    properties = session.query(Property).all()
    property_dicts = [p.to_dict() for p in properties]
    session.close()

    fraud_count = 0
    alert_count = 0

    for listing_data in all_listings:
        result = await _process_listing(listing_data, property_dicts)
        if result["fraud_status"] == "fraud" and result["confidence"] >= 0.7:
            fraud_count += 1
        if result["alert_sent"]:
            alert_count += 1

    session = SessionLocal()
    scan = session.query(ScanLog).filter_by(id=scan_id).first()
    if scan:
        scan.listings_found = len(all_listings)
        scan.fraud_found = fraud_count
        scan.alerts_sent = alert_count
        scan.status = "completed"
        scan.completed_at = datetime.now(timezone.utc)
        session.commit()
    session.close()

    return {
        "scan_id": str(scan_id),
        "listings_found": len(all_listings),
        "fraud_found": fraud_count,
        "alerts_sent": alert_count,
        "source": source or "all",
    }


# ─── FastAPI App Factory ─────────────────────────────────────────────────────

def create_app(static_dir: str) -> FastAPI:
    init_db()

    api = APIRouter()

    @api.get("/health")
    def health():
        return {"ok": True, "service": "rental-fraud-detector"}

    # ── Properties CRUD ──────────────────────────────────────────────────

    @api.get("/properties")
    def list_properties():
        session = SessionLocal()
        properties = session.query(Property).order_by(Property.created_at.desc()).all()
        result = [p.to_dict() for p in properties]
        session.close()
        return result

    @api.post("/properties")
    def create_property(data: PropertyCreate):
        session = SessionLocal()
        prop = Property(
            name=data.name,
            address=data.address,
            city=data.city,
            state=data.state,
            zip_code=data.zip_code,
            zip_plus4=data.zip_plus4,
            bedrooms=data.bedrooms,
            bathrooms=data.bathrooms,
            square_footage=data.square_footage,
            monthly_rent=data.monthly_rent,
            description=data.description,
            image_urls=",".join(data.image_urls) if data.image_urls else None,
            amenities=",".join(data.amenities) if data.amenities else None,
        )
        session.add(prop)
        session.commit()
        result = prop.to_dict()
        session.close()
        return result

    @api.put("/properties/{property_id}")
    def update_property(property_id: str, data: PropertyUpdate):
        session = SessionLocal()
        prop = session.query(Property).filter_by(id=uuid.UUID(property_id)).first()
        if not prop:
            session.close()
            raise HTTPException(status_code=404, detail="Property not found")
        if data.name is not None:
            prop.name = data.name
        if data.address is not None:
            prop.address = data.address
        if data.city is not None:
            prop.city = data.city
        if data.state is not None:
            prop.state = data.state
        if data.zip_code is not None:
            prop.zip_code = data.zip_code
        if data.zip_plus4 is not None:
            prop.zip_plus4 = data.zip_plus4
        if data.bedrooms is not None:
            prop.bedrooms = data.bedrooms
        if data.bathrooms is not None:
            prop.bathrooms = data.bathrooms
        if data.square_footage is not None:
            prop.square_footage = data.square_footage
        if data.monthly_rent is not None:
            prop.monthly_rent = data.monthly_rent
        if data.description is not None:
            prop.description = data.description
        if data.image_urls is not None:
            prop.image_urls = ",".join(data.image_urls)
        if data.amenities is not None:
            prop.amenities = ",".join(data.amenities)
        prop.updated_at = datetime.now(timezone.utc)
        session.commit()
        result = prop.to_dict()
        session.close()
        return result

    @api.delete("/properties/{property_id}")
    def delete_property(property_id: str):
        session = SessionLocal()
        prop = session.query(Property).filter_by(id=uuid.UUID(property_id)).first()
        if not prop:
            session.close()
            raise HTTPException(status_code=404, detail="Property not found")
        session.delete(prop)
        session.commit()
        session.close()
        return {"status": "deleted"}

    # ── Listings ──────────────────────────────────────────────────────────

    class ListingImport(BaseModel):
        text: str

    @api.post("/listings/import")
    async def import_listing(data: ListingImport):
        """Parse pasted listing text and run it through the fraud pipeline."""
        text = data.text.strip()
        if len(text) < 20:
            raise HTTPException(status_code=400, detail="Paste at least a sentence or two of listing text.")

        parsed = await parse_listing_text(text)
        listing_data = {
            "source": parsed["source"],
            "external_id": None,
            "title": parsed["title"],
            "price": parsed["price"],
            "location": parsed["location"],
            "description": parsed["description"],
            "url": parsed["url"],
            "image_urls": "",
            "posted_date": None,
        }

        session = SessionLocal()
        properties = session.query(Property).all()
        property_dicts = [p.to_dict() for p in properties]
        session.close()

        result = await _process_listing(listing_data, property_dicts)

        session = SessionLocal()
        listing = session.query(ScrapedListing).filter_by(id=uuid.UUID(result["listing_id"])).first()
        listing_dict = listing.to_dict() if listing else None
        session.close()

        return {"listing": listing_dict, "analysis": result}

    @api.get("/listings")
    def list_listings(fraud_status: str | None = None, limit: int = 100, offset: int = 0):
        session = SessionLocal()
        query = session.query(ScrapedListing).order_by(ScrapedListing.scraped_at.desc())
        if fraud_status:
            query = query.filter_by(fraud_status=ScrapeStatus(fraud_status))
        listings = query.limit(limit).offset(offset).all()
        result = [l.to_dict() for l in listings]
        session.close()
        return result

    @api.get("/listings/{listing_id}")
    def get_listing(listing_id: str):
        session = SessionLocal()
        listing = session.query(ScrapedListing).filter_by(id=uuid.UUID(listing_id)).first()
        session.close()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        return listing.to_dict(full=True)

    @api.delete("/listings/{listing_id}")
    def delete_listing(listing_id: str):
        session = SessionLocal()
        try:
            listing = session.query(ScrapedListing).filter_by(
                id=uuid.UUID(listing_id)
            ).first()
            if not listing:
                raise HTTPException(status_code=404, detail="Listing not found")
            session.query(Alert).filter_by(listing_id=listing.id).delete()
            session.delete(listing)
            session.commit()
        finally:
            session.close()
        return {"status": "deleted"}

    # ── Scan ──────────────────────────────────────────────────────────────

    @api.post("/scan")
    async def trigger_scan(source: str | None = "all"):
        result = await run_scan(source)
        return result

    @api.get("/scans")
    def list_scans(limit: int = 20):
        session = SessionLocal()
        scans = session.query(ScanLog).order_by(ScanLog.started_at.desc()).limit(limit).all()
        result = [s.to_dict() for s in scans]
        session.close()
        return result

    # ── Alerts ────────────────────────────────────────────────────────────

    @api.get("/alerts")
    def list_alerts(limit: int = 50):
        session = SessionLocal()
        alerts = session.query(Alert).order_by(Alert.created_at.desc()).limit(limit).all()
        result = [a.to_dict() for a in alerts]
        session.close()
        return result

    # ── Config Status ─────────────────────────────────────────────────────

    @api.get("/config/status")
    def config_status():
        return ConfigStatus(
            apify_configured=bool(APIFY_API_KEY),
            twilio_configured=bool(
                TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER
            ),
            twilio_enabled=TWILIO_ENABLED,
            gemini_configured=bool(os.environ.get("GEMINI_WORKSHOP_API_KEY")),
            scrape_city=SCRAPE_CITY,
            scrape_state=SCRAPE_STATE,
            alert_phone=ALERT_PHONE_NUMBER,
        )

    # ── Stats/Dashboard ───────────────────────────────────────────────────

    @api.get("/stats")
    def get_stats():
        session = SessionLocal()
        total_properties = session.query(Property).count()
        total_listings = session.query(ScrapedListing).count()
        fraud_listings = session.query(ScrapedListing).filter_by(
            fraud_status=ScrapeStatus.FRAUD
        ).count()
        total_alerts = session.query(Alert).filter(Alert.status == "sent").count()
        last_scan = (
            session.query(ScanLog)
            .order_by(ScanLog.started_at.desc())
            .first()
        )
        result = {
            "total_properties": total_properties,
            "total_listings_scraped": total_listings,
            "fraud_detected": fraud_listings,
            "alerts_sent": total_alerts,
            "last_scan": last_scan.to_dict() if last_scan else None,
        }
        session.close()
        return result

    # ── Build app ─────────────────────────────────────────────────────────

    app = FastAPI(title="fillory fraud detector")
    app.include_router(api, prefix="/api")

    if os.path.isdir(static_dir):
        assets_dir = os.path.join(static_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}")
        async def spa_fallback(request: Request, path: str):
            file_path = os.path.join(static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(
                os.path.join(static_dir, "index.html"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

    return app
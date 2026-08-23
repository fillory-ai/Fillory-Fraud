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
    Case,
    CaseStatus,
    RESOLUTION_CODES,
)
from detector import parse_listing_text
from pipeline import run_scan, process_listing
from scheduler import start_scheduler, shutdown_scheduler, scheduler_status, scan_health
from config import (
    APIFY_API_KEY,
    TWILIO_ENABLED,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    ALERT_PHONE_NUMBER,
    SCRAPE_CITY,
    SCRAPE_STATE,
    OBSERVE_MODE,
    SCHEDULER_ENABLED,
    SCAN_INTERVAL_HOURS,
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
    observe_mode: bool = True
    scheduler_enabled: bool = False
    scan_interval_hours: float = 4


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

        result = await process_listing(listing_data, property_dicts)

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
        result = await run_scan(source, trigger="manual")
        return result

    @api.get("/scans")
    def list_scans(limit: int = 20):
        session = SessionLocal()
        scans = session.query(ScanLog).order_by(ScanLog.started_at.desc()).limit(limit).all()
        result = [s.to_dict() for s in scans]
        session.close()
        return result

    @api.get("/scans/health")
    def scans_health():
        """Whether we are actually seeing the market. Surfaced in the UI so a
        blocked scraper can't masquerade as 'no fraud found'."""
        return {**scan_health(), "scheduler": scheduler_status()}

    # ── Cases ─────────────────────────────────────────────────────────────

    class CaseUpdate(BaseModel):
        status: str
        # Why, not just what. Optional on the wire so acknowledging a case
        # stays a one-click action, but required by the rule below for the
        # closing transitions where the answer is the tuning signal.
        resolution_code: str | None = None
        resolution_note: str | None = None

    # Closing a case without saying why throws away the only label we ever get.
    _CLOSING_STATUSES = (CaseStatus.RESOLVED, CaseStatus.DISMISSED)

    def _case_payload(session, case):
        """The shape the review queue renders.

        Shared by GET and PUT deliberately: PUT used to return a bare
        `case.to_dict()`, and the frontend swapping that into its list turned
        a rich row into "Listing removed / —" the instant an operator
        acknowledged a case.
        """
        listing = session.query(ScrapedListing).filter_by(id=case.listing_id).first()
        prop = session.query(Property).filter_by(id=case.property_id).first()
        alerts = session.query(Alert).filter_by(case_id=case.id).count()
        return {
            **case.to_dict(),
            "alerts_recorded": alerts,
            "listing": listing.to_dict() if listing else None,
            "property_name": prop.name if prop else None,
        }

    @api.get("/cases")
    def list_cases(status: str | None = None, limit: int = 100):
        """Cases, richest-first, with their listing and property inlined —
        the review queue is the screen someone actually works from."""
        session = SessionLocal()
        query = session.query(Case).order_by(Case.opened_at.desc())
        if status:
            try:
                query = query.filter(Case.status == CaseStatus(status))
            except ValueError:
                session.close()
                raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
        cases = query.limit(limit).all()

        result = [_case_payload(session, case) for case in cases]
        session.close()
        return result

    @api.put("/cases/{case_id}")
    def update_case(case_id: str, data: CaseUpdate):
        """Move a case through its lifecycle. Dismissing or resolving one also
        silences it permanently — that is the point."""
        try:
            new_status = CaseStatus(data.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status '{data.status}'")

        if data.resolution_code and data.resolution_code not in RESOLUTION_CODES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown resolution_code '{data.resolution_code}'. "
                       f"Expected one of: {', '.join(sorted(RESOLUTION_CODES))}",
            )
        if new_status in _CLOSING_STATUSES and not data.resolution_code:
            raise HTTPException(
                status_code=400,
                detail=f"resolution_code is required when setting status to "
                       f"'{data.status}' — closing a case is the only moment we "
                       f"learn whether the detector was right.",
            )

        session = SessionLocal()
        try:
            case = session.query(Case).filter_by(id=uuid.UUID(case_id)).first()
            if not case:
                raise HTTPException(status_code=404, detail="Case not found")
            case.status = new_status
            case.updated_at = datetime.now(timezone.utc)
            if data.resolution_code:
                case.resolution_code = data.resolution_code
            if data.resolution_note is not None:
                case.resolution_note = data.resolution_note
            if new_status in _CLOSING_STATUSES:
                case.resolved_at = datetime.now(timezone.utc)
            session.commit()
            return _case_payload(session, case)
        finally:
            session.close()

    @api.get("/cases/resolution-codes")
    def resolution_codes():
        """The disposition vocabulary, served so the UI can't drift from it."""
        return [{"code": c, "label": label} for c, label in RESOLUTION_CODES.items()]

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
            observe_mode=OBSERVE_MODE,
            scheduler_enabled=SCHEDULER_ENABLED,
            scan_interval_hours=SCAN_INTERVAL_HOURS,
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
        open_cases = session.query(Case).filter(
            Case.status.in_([CaseStatus.OPEN, CaseStatus.ACKNOWLEDGED])
        ).count()
        result = {
            "total_properties": total_properties,
            "total_listings_scraped": total_listings,
            "fraud_detected": fraud_listings,
            "alerts_sent": total_alerts,
            "open_cases": open_cases,
            "last_scan": last_scan.to_dict() if last_scan else None,
            "scan_health": scan_health(),
            "observe_mode": OBSERVE_MODE,
        }
        session.close()
        return result

    # ── Build app ─────────────────────────────────────────────────────────

    app = FastAPI(title="fillory fraud detector")
    app.include_router(api, prefix="/api")

    @app.on_event("startup")
    def _startup():
        start_scheduler()

    @app.on_event("shutdown")
    def _shutdown():
        shutdown_scheduler()

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
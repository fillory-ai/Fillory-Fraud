"""Evidence and model-governance regression suite.

Covers the three things an accusatory product has to be able to answer:
  1. What exactly did the model see when it called this fraud?
  2. Which model and which prompt produced that verdict?
  3. When a human closed the case, what did they say the detector got wrong?

The first is the one with teeth. scraped_listings rows are mutated in place on
re-sighting, so before this suite existed a scammer editing their advert
destroyed the evidence behind the accusation against them.

Creates rows with a `test-gov-` prefix and deletes them at the end. The AI call
is stubbed, so this is deterministic and costs nothing.

Run: uv run python test_governance.py
"""
import asyncio
import json
import uuid

import pipeline
from database import SessionLocal
from models import Property, ScrapedListing, Alert, Case, CaseStatus, RESOLUTION_CODES

PREFIX = "test-gov-"

failures: list[str] = []


def check(name: str, got, expected):
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(name)


def make_property(session) -> Property:
    prop = Property(
        name=f"{PREFIX}property",
        address="4411 NE Killingsworth St Unit 107",
        city="Portland",
        state="OR",
        zip_code="97218",
        bedrooms=3,
        bathrooms=1.5,
        monthly_rent=2995.0,
    )
    session.add(prop)
    session.commit()
    session.refresh(prop)
    return prop


def listing(**overrides) -> dict:
    base = {
        "source": "craigslist",
        "external_id": f"{PREFIX}listing-1",
        "title": "3BR townhouse, urgent",
        "price": 1200.0,
        "location": "Portland, OR",
        "street_address": "4411 NE Killingsworth St",
        "description": "Owner abroad, wire deposit via Zelle to hold.",
        "url": "https://example.com/gov-1",
        "image_urls": "https://example.com/a.jpg",
        "posted_date": None,
        "latitude": 45.5628691,
        "longitude": -122.6180357,
    }
    base.update(overrides)
    return base


def cleanup(session, prop_id):
    ids = [
        r.id for r in session.query(ScrapedListing)
        .filter(ScrapedListing.external_id.like(f"{PREFIX}%")).all()
    ]
    if ids:
        session.query(Alert).filter(Alert.listing_id.in_(ids)).delete(synchronize_session=False)
        session.query(Case).filter(Case.listing_id.in_(ids)).delete(synchronize_session=False)
        session.query(ScrapedListing).filter(ScrapedListing.id.in_(ids)).delete(
            synchronize_session=False
        )
    session.query(Property).filter(Property.id == prop_id).delete(synchronize_session=False)
    session.commit()


async def main():
    session = SessionLocal()
    prop = make_property(session)
    cleanup_needed = True

    verdict = {
        "fraud_status": "fraud",
        "confidence": 0.95,
        "reason": "stubbed verdict for governance test",
        "matched_property_id": str(prop.id),
        "match_signal": "test",
        "model_name": "gemini-3.5-flash",
        "prompt_version": "abc123def456",
    }

    async def fake_analyze(listing_data, properties):
        return dict(verdict)

    # pipeline imports this as `analyze_listing_ai`; patching the bare name
    # silently does nothing and the suite quietly hits the real API instead.
    pipeline.analyze_listing_ai = fake_analyze
    property_dicts = [prop.to_dict()]

    try:
        # ── 1. Evidence is captured at case open ────────────────────────────
        original = listing()
        r1 = await pipeline.process_listing(original, property_dicts)
        check("case opened", r1["case_action"], "opened")

        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()
        session.expire_all()
        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()

        check("opening_evidence written", case.opening_evidence is not None, True)
        ev = json.loads(case.opening_evidence)
        check("evidence holds the title as judged", ev["title"], original["title"])
        check("evidence holds the price as judged", ev["price"], 1200.0)
        check("evidence holds the description as judged",
              ev["description"], original["description"])
        check("evidence records when it was captured", "captured_at" in ev, True)
        check("evidence records the fingerprint", "content_fingerprint" in ev, True)

        # ── 2. Model and prompt attribution ─────────────────────────────────
        check("case records which model answered", case.model_name, "gemini-3.5-flash")
        check("case records the prompt version", case.prompt_version, "abc123def456")

        # ── 3. THE POINT: the scammer edits the advert ──────────────────────
        # Re-scraping overwrites scraped_listings in place. The evidence behind
        # the original accusation must survive that.
        edited = listing(
            title="Lovely 3BR home",
            price=2995.0,
            description="Standard listing text, nothing suspicious at all.",
        )
        await pipeline.process_listing(edited, property_dicts)
        session.expire_all()

        db_listing = session.query(ScrapedListing).filter_by(
            id=uuid.UUID(r1["listing_id"])
        ).first()
        check("live listing row was overwritten by the edit",
              db_listing.description, edited["description"])

        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()
        ev_after = json.loads(case.opening_evidence)
        check("opening evidence survived the edit — title",
              ev_after["title"], original["title"])
        check("opening evidence survived the edit — price", ev_after["price"], 1200.0)
        check("opening evidence survived the edit — description",
              ev_after["description"], original["description"])
        check("opening evidence is byte-identical to what we first stored",
              ev_after, ev)

        # ── 4. Every alert carries its own snapshot ─────────────────────────
        alerts = (
            session.query(Alert)
            .filter_by(listing_id=uuid.UUID(r1["listing_id"]))
            .order_by(Alert.created_at.asc())
            .all()
        )
        check("at least one alert recorded", len(alerts) >= 1, True)
        check("alert carries an evidence snapshot",
              alerts[0].evidence_snapshot is not None, True)
        alert_ev = json.loads(alerts[0].evidence_snapshot)
        check("alert snapshot matches what was alerted on",
              alert_ev["title"], original["title"])
        check("alert to_dict parses the snapshot into an object",
              isinstance(alerts[0].to_dict()["evidence_snapshot"], dict), True)

        # ── 5. Model attribution updates with the verdict ───────────────────
        # opening_evidence is write-once; model_name is not — it describes the
        # current verdict and moves with confidence and reason.
        verdict["model_name"] = "gemini-3.1-flash-lite"
        verdict["prompt_version"] = "999fedcba000"
        await pipeline.process_listing(
            listing(price=800.0, description="changed again to force reanalysis"),
            property_dicts,
        )
        session.expire_all()
        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()
        check("model attribution follows the fallback that actually answered",
              case.model_name, "gemini-3.1-flash-lite")
        check("prompt version updates with the verdict",
              case.prompt_version, "999fedcba000")
        check("opening evidence still untouched after three re-scrapes",
              json.loads(case.opening_evidence)["price"], 1200.0)

        # ── 6. A no-match verdict is still attributed ───────────────────────
        # detector.analyze_listing returns early without consulting a model when
        # nothing matches; it must still stamp the prompt version so we can tell
        # which build of the matcher produced the "no match".
        import detector
        check("prompt version is a stable 12-char hash",
              len(detector._PROMPT_VERSION), 12)
        check("prompt version is deterministic across calls",
              detector._compute_prompt_version(), detector._PROMPT_VERSION)

        # ── 7. Disposition vocabulary ───────────────────────────────────────
        check("false-positive codes distinguish the three failure modes",
              sorted(c for c in RESOLUTION_CODES if c.startswith("false_positive")),
              ["false_positive_authorized", "false_positive_legitimate",
               "false_positive_match"])
        check("confirmed_fraud is available as a positive label",
              "confirmed_fraud" in RESOLUTION_CODES, True)

        case.status = CaseStatus.DISMISSED
        case.resolution_code = "false_positive_match"
        case.resolution_note = "geo radius caught the neighbouring building"
        session.commit()
        session.expire_all()
        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()
        payload = case.to_dict()
        check("resolution code round-trips", payload["resolution_code"],
              "false_positive_match")
        check("resolution note round-trips", payload["resolution_note"],
              "geo radius caught the neighbouring building")
        check("case payload exposes the opening evidence as an object",
              isinstance(payload["opening_evidence"], dict), True)
        check("case payload exposes model attribution",
              payload["model_name"], "gemini-3.1-flash-lite")

        # ── 8. Malformed evidence must not break a list endpoint ────────────
        case.opening_evidence = "{not valid json"
        session.commit()
        session.expire_all()
        case = session.query(Case).filter_by(listing_id=uuid.UUID(r1["listing_id"])).first()
        check("malformed evidence degrades to None rather than raising",
              case.to_dict()["opening_evidence"], None)

    finally:
        if cleanup_needed:
            cleanup(session, prop.id)
        session.close()

    print()
    if failures:
        print(f"{len(failures)} governance check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("all governance checks passed")


if __name__ == "__main__":
    asyncio.run(main())

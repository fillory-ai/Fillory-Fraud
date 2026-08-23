"""M1 regression suite: listing identity, case dedup, alert suppression.

Runs against the real database inside a transaction-ish scope: it creates a
throwaway property and listings with a `test-m1-` prefix and deletes them at
the end. The AI call is stubbed, so this is deterministic and costs nothing.

Run: uv run python test_pipeline.py
"""
import asyncio
import uuid

import pipeline
from database import SessionLocal
from models import Property, ScrapedListing, Alert, Case, CaseStatus

PREFIX = "test-m1-"

_fake_verdict = {
    "fraud_status": "fraud",
    "confidence": 0.95,
    "reason": "stubbed verdict for pipeline test",
    "matched_property_id": None,   # filled in by the fixture
    "match_signal": "test",
}

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
        monthly_rent=2995,
    )
    session.add(prop)
    session.commit()
    return prop


def listing(**over) -> dict:
    base = {
        "source": "craigslist",
        "external_id": f"{PREFIX}listing-1",
        "title": "3BR townhome for rent",
        "price": 900.0,
        "location": "Portland, OR",
        "description": "Beautiful 3 bedroom at 4411 NE Killingsworth St",
        "url": "https://example.com/test-m1",
        "image_urls": "https://example.com/a.jpg",
        "enriched": False,
    }
    base.update(over)
    return base


def cleanup(session, property_id):
    listing_ids = [
        row.id for row in session.query(ScrapedListing)
        .filter(ScrapedListing.external_id.like(f"{PREFIX}%")).all()
    ]
    if listing_ids:
        session.query(Case).filter(Case.listing_id.in_(listing_ids)).delete(synchronize_session=False)
        session.query(Alert).filter(Alert.listing_id.in_(listing_ids)).delete(synchronize_session=False)
        session.query(ScrapedListing).filter(ScrapedListing.id.in_(listing_ids)).delete(synchronize_session=False)
    session.query(Case).filter(Case.property_id == property_id).delete(synchronize_session=False)
    session.query(Property).filter(Property.id == property_id).delete(synchronize_session=False)
    session.commit()


async def main():
    session = SessionLocal()
    prop = make_property(session)
    property_id = prop.id
    property_dicts = [prop.to_dict()]
    session.close()

    _fake_verdict["matched_property_id"] = str(property_id)

    calls = {"n": 0}

    async def fake_analyze(listing_data, properties):
        calls["n"] += 1
        return dict(_fake_verdict)

    real_analyze = pipeline.analyze_listing_ai
    pipeline.analyze_listing_ai = fake_analyze

    try:
        # 1. First sighting: inserts, analyses, opens a case, records an alert.
        r1 = await pipeline.process_listing(listing(), property_dicts)
        check("first sighting is new", r1["is_new"], True)
        check("first sighting opens a case", r1["case_action"], "opened")
        check("first sighting analysed", calls["n"], 1)

        # 2. Identical re-sighting: no new row, no AI call, no alert.
        r2 = await pipeline.process_listing(listing(), property_dicts)
        check("re-sighting is not new", r2["is_new"], False)
        check("re-sighting skips AI", calls["n"], 1)
        check("re-sighting is silent", r2["case_action"], "quiet")

        session = SessionLocal()
        row = session.query(ScrapedListing).filter_by(
            source="craigslist", external_id=f"{PREFIX}listing-1"
        ).first()
        check("single row for two sightings", row.times_seen, 2)
        check(
            "no duplicate listing rows",
            session.query(ScrapedListing).filter(
                ScrapedListing.external_id == f"{PREFIX}listing-1"
            ).count(),
            1,
        )
        check(
            "one case only",
            session.query(Case).filter_by(property_id=property_id).count(),
            1,
        )
        check(
            "one alert only",
            session.query(Alert).filter_by(listing_id=row.id).count(),
            1,
        )
        listing_id = row.id
        session.close()

        # 3. Material change: re-analysed, case updated, second alert allowed
        #    (cooldown has not started because observe mode never "sent").
        r3 = await pipeline.process_listing(listing(price=1200.0), property_dicts)
        check("changed listing re-analysed", calls["n"], 2)
        check("changed listing updates case", r3["case_action"], "changed")

        session = SessionLocal()
        case = session.query(Case).filter_by(property_id=property_id).first()
        check("change is logged", bool(case.change_log), True)
        check(
            "still one case after change",
            session.query(Case).filter_by(property_id=property_id).count(),
            1,
        )

        # 4. A dismissed case never speaks again, even on further changes.
        case.status = CaseStatus.DISMISSED
        session.commit()
        session.close()

        r4 = await pipeline.process_listing(listing(price=1500.0), property_dicts)
        check("dismissed case stays quiet", r4["case_action"], "quiet")

        # 5. Delisting: a scan that no longer returns this external_id flags it.
        session = SessionLocal()
        scan_started = pipeline._now()
        # last_seen_at must predate the scan window for the row to be eligible
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        row.last_seen_at = scan_started - pipeline.timedelta(minutes=5)
        session.commit()
        n = pipeline._mark_delisted(session, "craigslist", {"some-other-id"}, scan_started)
        check("absent listing marked delisted", n >= 1, True)
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("delisted_at set", row.delisted_at is not None, True)

        # 6. Seeing it again clears the delisted flag.
        session.close()
        await pipeline.process_listing(listing(price=1500.0), property_dicts)
        session = SessionLocal()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("re-sighting clears delisted_at", row.delisted_at is None, True)
        session.close()

        # 7. Fingerprint ignores fields that drift without meaning.
        a = pipeline.content_fingerprint(listing())
        b = pipeline.content_fingerprint(listing(enriched=True))
        check("fingerprint ignores enrichment flag", a, b)
        c = pipeline.content_fingerprint(listing(price=1.0))
        check("fingerprint tracks price", a != c, True)

    finally:
        pipeline.analyze_listing_ai = real_analyze
        session = SessionLocal()
        cleanup(session, property_id)
        session.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        raise SystemExit(1)
    print("all pipeline checks passed")


if __name__ == "__main__":
    asyncio.run(main())

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
    from models import ScanLog
    session.query(ScanLog).filter(ScanLog.source.like(f"{PREFIX}%")).delete(synchronize_session=False)
    session.commit()


def _seed_scan_history(session, source, counts):
    """Give the coverage guard something to compare against.

    Keyed by a test-only source name so real scan logs can never satisfy or
    pollute the window.
    """
    import json
    from models import ScanLog
    for c in counts:
        session.add(ScanLog(
            source=f"{PREFIX}scan",
            status="completed",
            trigger="manual",
            listings_found=c,
            source_counts=json.dumps({source: c}),
        ))
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

        # 3. Material change inside the cooldown: re-analysed and the case is
        #    updated, but no second alert. Observe mode counts here — the
        #    original bug was that `last_alert_at` was only written on "sent",
        #    so the cooldown never engaged and every change re-alerted.
        r3 = await pipeline.process_listing(listing(price=1200.0), property_dicts)
        check("changed listing re-analysed", calls["n"], 2)
        check("material change inside cooldown stays quiet", r3["case_action"], "quiet")

        session = SessionLocal()
        case = session.query(Case).filter_by(property_id=property_id).first()
        check("change is logged even when quiet", bool(case.change_log), True)
        check("observe-mode alert started the cooldown", case.last_alert_at is not None, True)
        check(
            "no second alert inside cooldown",
            session.query(Alert).filter_by(listing_id=listing_id).count(),
            1,
        )
        check(
            "still one case after change",
            session.query(Case).filter_by(property_id=property_id).count(),
            1,
        )

        # 3b. Once the cooldown has elapsed, a further material change alerts.
        case.last_alert_at = pipeline._now() - pipeline.timedelta(
            hours=pipeline.ALERT_COOLDOWN_HOURS + 1
        )
        session.commit()
        session.close()

        r3b = await pipeline.process_listing(listing(price=1300.0), property_dicts)
        check("material change after cooldown alerts", r3b["case_action"], "changed")
        check("observe-mode alert is recorded, not sent", r3b["alert_status"], "observed")
        session = SessionLocal()
        check(
            "second alert recorded after cooldown",
            session.query(Alert).filter_by(listing_id=listing_id).count(),
            2,
        )
        case = session.query(Case).filter_by(property_id=property_id).first()
        check("alert_count counts observed alerts", case.alert_count, 2)

        # 3c. Rate cap applies in observe mode too, so the volume you watch is
        #     the volume you would have received.
        real_cap = pipeline.MAX_ALERTS_PER_DAY
        pipeline.MAX_ALERTS_PER_DAY = 0
        case.last_alert_at = pipeline._now() - pipeline.timedelta(
            hours=pipeline.ALERT_COOLDOWN_HOURS + 1
        )
        session.commit()
        session.close()
        try:
            r3c = await pipeline.process_listing(listing(price=1400.0), property_dicts)
            check("rate cap suppresses in observe mode", r3c["alert_status"], "suppressed_rate_limit")
        finally:
            pipeline.MAX_ALERTS_PER_DAY = real_cap
        session = SessionLocal()
        case = session.query(Case).filter_by(property_id=property_id).first()
        check("suppressed alert still starts the cooldown", case.last_alert_at is not None, True)
        check("suppressed alert does not count against the budget", case.alert_count, 2)

        # 3d. A notifier that raises is recorded as a failed alert, never
        #     propagated, and does not start the cooldown — so the next change
        #     retries instead of going silent.
        real_observe = pipeline.OBSERVE_MODE
        real_sender = pipeline.send_fraud_alert

        def exploding_sender(listing_data, property_name):
            raise RuntimeError("twilio is on fire")

        pipeline.OBSERVE_MODE = False
        pipeline.send_fraud_alert = exploding_sender
        case.last_alert_at = None
        session.commit()
        session.close()
        try:
            r3d = await pipeline.process_listing(listing(price=1450.0), property_dicts)
            check("notifier exception recorded as failed", r3d["alert_status"], "failed")
            check("notifier exception does not mark sent", r3d["alert_sent"], False)
        finally:
            pipeline.OBSERVE_MODE = real_observe
            pipeline.send_fraud_alert = real_sender
        session = SessionLocal()
        case = session.query(Case).filter_by(property_id=property_id).first()
        check("failed send leaves the cooldown unstarted (retry allowed)",
              case.last_alert_at is None, True)
        failed = (
            session.query(Alert)
            .filter_by(listing_id=listing_id, status="failed")
            .count()
        )
        check("failed alert is still audited", failed, 1)

        # 4. A dismissed case never speaks again, even on further changes.
        case.status = CaseStatus.DISMISSED
        session.commit()
        session.close()

        r4 = await pipeline.process_listing(listing(price=1500.0), property_dicts)
        check("dismissed case stays quiet", r4["case_action"], "quiet")

        # 5. Delisting. Both guards, because marking a live scam "delisted"
        #    drops it out of monitoring silently.
        session = SessionLocal()
        scan_started = pipeline._now()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        row.last_seen_at = scan_started - pipeline.timedelta(minutes=5)
        row.consecutive_misses = 0
        row.delisted_at = None
        session.commit()

        real_average = pipeline._recent_source_average

        # 5a. No scan history at all → refuse to delist rather than guess.
        pipeline._recent_source_average = lambda *a, **k: None
        n = pipeline._mark_delisted(session, "craigslist", {"other-1"}, scan_started, row_count=1)
        check("no scan history means no delisting", n, 0)
        session.expire_all()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("no miss counted when the guard declined", row.consecutive_misses, 0)

        # 5b. A scan far below the recent average is a truncated scrape, not an
        #     emptied market. This is the Apify "20 rows instead of 50" case.
        pipeline._recent_source_average = lambda *a, **k: 50.0
        n = pipeline._mark_delisted(session, "craigslist", {"other-1"}, scan_started, row_count=5)
        check("short scrape does not delist", n, 0)
        session.expire_all()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("short scrape counts no miss", row.consecutive_misses, 0)

        # 5c. A full scan counts one miss but does not yet delist.
        n = pipeline._mark_delisted(session, "craigslist", {"other-1"}, scan_started, row_count=50)
        check("first miss does not delist", n, 0)
        session.expire_all()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("first miss is counted", row.consecutive_misses, 1)
        check("first miss leaves delisted_at unset", row.delisted_at is None, True)

        # 5d. The second consecutive miss does.
        n = pipeline._mark_delisted(session, "craigslist", {"other-1"}, scan_started, row_count=50)
        check("second consecutive miss delists", n, 1)
        session.expire_all()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("delisted_at set after threshold", row.delisted_at is not None, True)

        pipeline._recent_source_average = real_average

        # 5e. The average itself: computed from the recorded per-source counts
        #     of recent completed scans.
        test_source = f"{PREFIX}src"
        _seed_scan_history(session, test_source, [40, 60])
        check("coverage average reads recorded scan counts",
              pipeline._recent_source_average(session, test_source), 50.0)
        check("unknown source has no history",
              pipeline._recent_source_average(session, f"{PREFIX}never"), None)

        # 6. Seeing it again clears the flag and the miss streak.
        session.close()
        await pipeline.process_listing(listing(price=1500.0), property_dicts)
        session = SessionLocal()
        row = session.query(ScrapedListing).filter_by(id=listing_id).first()
        check("re-sighting clears delisted_at", row.delisted_at is None, True)
        check("re-sighting resets the miss streak", row.consecutive_misses, 0)

        # 6b. Losing the insert race is not an error: the second claim on the
        #     same (source, external_id) returns None instead of raising.
        fp = pipeline.content_fingerprint(listing())
        claimed = pipeline._claim_listing(session, listing(), fp, pipeline._now())
        check("duplicate claim is refused, not raised", claimed, None)
        check(
            "still exactly one row after a losing claim",
            session.query(ScrapedListing).filter_by(
                source="craigslist", external_id=f"{PREFIX}listing-1"
            ).count(),
            1,
        )
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

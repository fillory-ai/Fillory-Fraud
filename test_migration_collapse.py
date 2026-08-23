"""Prove the M1 duplicate-collapse migration on a throwaway schema.

The development database has no duplicates (measured: 191 rows, 0 duplicates),
so the collapse in revision 7980ad450dea has never actually executed against
data. This builds the schema at the pre-M1 baseline inside a scratch Postgres
schema, plants duplicates whose *later* sighting holds the real fraud verdict,
runs the upgrade, and checks that the surviving row kept the newest content and
the verdict rather than the stalest row's.

Run: uv run python test_migration_collapse.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

SCHEMA = "collapse_test"
os.environ["DB_SEARCH_PATH"] = SCHEMA

from sqlalchemy import create_engine, text  # noqa: E402

BASE_URL = os.environ.get("DBFB9343D8_DATABASE_URL")
if not BASE_URL:
    import keyring
    BASE_URL = keyring.get_password("workshop", "DBFB9343D8_DATABASE_URL")
    os.environ["DBFB9343D8_DATABASE_URL"] = BASE_URL

admin = create_engine(BASE_URL, pool_pre_ping=True)


def run(sql, **params):
    with admin.begin() as conn:
        conn.execute(text(f"SET search_path TO {SCHEMA}"))
        result = conn.execute(text(sql), params)
        return result.fetchall() if result.returns_rows else []


def alembic_cfg():
    from alembic.config import Config
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    return cfg


def main():
    from alembic import command

    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))

    failures = []

    def check(label, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    try:
        cfg = alembic_cfg()
        # Baseline only — the state a pre-M1 deployment is in.
        command.upgrade(cfg, "372243be407d")
        check("baseline upgrade creates scraped_listings",
              bool(run("SELECT 1 FROM information_schema.tables "
                       "WHERE table_schema=:s AND table_name='scraped_listings'", s=SCHEMA)))

        now = datetime.now(timezone.utc)
        old_id, new_id = uuid.uuid4(), uuid.uuid4()
        alert_id = uuid.uuid4()

        # Two sightings of the same posting. The earliest row is the one the
        # migration keeps; the later one holds the truth.
        ins = (
            "INSERT INTO scraped_listings "
            "(id, source, external_id, title, price, description, url, "
            " fraud_status, fraud_confidence, fraud_reason, created_at, scraped_at, enriched) "
            "VALUES (:id, 'craigslist', 'dupe-1', :title, :price, :desc, :url, "
            " :status, :conf, :reason, :created, :scraped, :enriched)"
        )
        run(ins, id=old_id, title="Old title", price=1000.0, desc="stale body",
            url="https://example.com/old", status="UNKNOWN", conf=None, reason=None,
            created=now - timedelta(days=2), scraped=now - timedelta(days=2), enriched=False)
        run(ins, id=new_id, title="New title", price=2500.0, desc="fresh body",
            url="https://example.com/new", status="FRAUD", conf=0.95,
            reason="impersonates registered property",
            created=now, scraped=now, enriched=True)
        run("INSERT INTO alerts (id, listing_id, alert_type, recipient, message, status, created_at) "
            "VALUES (:id, :lid, 'sms', '+15550000000', 'test', 'sent', :now)",
            id=alert_id, lid=new_id, now=now)

        command.upgrade(cfg, "head")

        rows = run("SELECT id, title, price, description, url, fraud_status, fraud_confidence, "
                   "fraud_reason, first_seen_at, last_seen_at, times_seen, enriched "
                   "FROM scraped_listings WHERE external_id='dupe-1'")
        check("collapsed to a single row", len(rows) == 1)
        if rows:
            r = rows[0]
            check("kept the earliest row's id (alerts already point at it)", r[0] == old_id)
            check("merged the newest title", r[1] == "New title")
            check("merged the newest price", r[2] == 2500.0)
            check("merged the newest description", r[3] == "fresh body")
            check("merged the newest url", r[4] == "https://example.com/new")
            check("kept the fraud verdict from the row that had one", str(r[5]) == "FRAUD")
            check("kept the verdict confidence", r[6] == 0.95)
            check("kept the verdict reason", r[7] == "impersonates registered property")
            check("first_seen_at is the oldest sighting",
                  r[8] is not None and abs((r[8] - (now - timedelta(days=2))).total_seconds()) < 2)
            check("last_seen_at is the newest sighting (scraped_at, not created_at)",
                  r[9] is not None and abs((r[9] - now).total_seconds()) < 2)
            check("times_seen counts both sightings", r[10] == 2)
            check("enrichment survives the merge", r[11] is True)

        moved = run("SELECT listing_id FROM alerts WHERE id=:id", id=alert_id)
        check("alert repointed to the surviving row", moved and moved[0][0] == old_id)

        idx = run("SELECT indexname FROM pg_indexes "
                  "WHERE schemaname=:s AND indexname='uq_listing_source_external'", s=SCHEMA)
        check("unique index created after the collapse", bool(idx))

        # Fresh-database path: the whole chain must also run on an empty schema.
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        check("downgrade to base then upgrade to head succeeds", True)
        check("consecutive_misses column present",
              bool(run("SELECT 1 FROM information_schema.columns WHERE table_schema=:s "
                       "AND table_name='scraped_listings' AND column_name='consecutive_misses'", s=SCHEMA)))
        check("scan_logs.source_counts column present",
              bool(run("SELECT 1 FROM information_schema.columns WHERE table_schema=:s "
                       "AND table_name='scan_logs' AND column_name='source_counts'", s=SCHEMA)))
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + "; ".join(failures))
        sys.exit(1)
    print("All migration collapse checks passed.")


if __name__ == "__main__":
    main()

"""End-to-end check that PUT /api/cases/{id} returns the same shape as GET.

The frontend swaps the PUT response straight into its case list; when PUT
returned a bare case.to_dict() the row degraded to "Listing removed / —" the
moment an operator acknowledged something.

Run with the dev server up: uv run python test_case_api.py
"""
import json
import os
import sys
import urllib.request
import uuid

BASE = os.environ.get("API_BASE", "http://localhost:3176")

from database import SessionLocal  # noqa: E402
from models import Case, CaseStatus, Property, ScrapedListing  # noqa: E402

PREFIX = "test-api-"
failures = []


def check(name, got, expected):
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(name)


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    session = SessionLocal()
    prop = Property(name=f"{PREFIX}prop", address="1 Test St", city="Portland",
                    state="OR", zip_code="97218")
    listing = ScrapedListing(source="craigslist", external_id=f"{PREFIX}l1",
                             title=f"{PREFIX}listing", url="https://example.com/x")
    session.add_all([prop, listing])
    session.commit()
    case = Case(listing_id=listing.id, property_id=prop.id, status=CaseStatus.OPEN,
                confidence=0.9, reason="test", match_signal="test")
    session.add(case)
    session.commit()
    case_id, prop_id, listing_id = case.id, prop.id, listing.id
    session.close()

    try:
        listed = [c for c in request("GET", "/api/cases") if c["id"] == str(case_id)]
        check("case appears in GET /api/cases", len(listed), 1)
        got = listed[0]
        updated = request("PUT", f"/api/cases/{case_id}", {"status": "acknowledged"})

        check("PUT and GET return the same keys", sorted(updated), sorted(got))
        check("PUT keeps the listing inlined", updated["listing"] is not None, True)
        check("PUT keeps the listing title", updated["listing"]["title"], f"{PREFIX}listing")
        check("PUT keeps the property name", updated["property_name"], f"{PREFIX}prop")
        check("PUT reports alerts_recorded", updated["alerts_recorded"], 0)
        check("PUT applied the status change", updated["status"], "acknowledged")

        try:
            request("PUT", f"/api/cases/{case_id}", {"status": "not-a-status"})
            check("invalid status rejected", "no error", "HTTP 400")
        except urllib.error.HTTPError as e:
            check("invalid status rejected", e.code, 400)
    finally:
        session = SessionLocal()
        session.query(Case).filter(Case.id == case_id).delete()
        session.query(ScrapedListing).filter(ScrapedListing.id == listing_id).delete()
        session.query(Property).filter(Property.id == prop_id).delete()
        session.commit()
        session.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("all case API checks passed")


if __name__ == "__main__":
    main()

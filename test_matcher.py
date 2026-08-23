"""Matcher regression suite.

Covers the ten cases verified when geo matching landed, plus the ZIP+4 signal
and the bare-ZIP false positive that matched a roommate-wanted post to a
registered townhome.

Run: uv run python test_matcher.py
"""
from detector import _find_best_address_match

KILLINGSWORTH = {
    "id": "prop-1",
    "name": "3BR Townhome",
    "address": "4411 NE Killingsworth St Unit 107",
    "city": "Portland",
    "state": "OR",
    "zip_code": "97218",
    "zip_plus4": "97218-1234",
    "latitude": 45.5628691,
    "longitude": -122.6180357,
}

# Same ZIP+4 is intentionally absent here: this property exists to prove that
# a listing matching one property does not bleed onto another.
AUSTIN = {
    "id": "prop-2",
    "name": "123 Main St Rental",
    "address": "123 Main St",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "zip_plus4": None,
    "latitude": 30.3026459,
    "longitude": -97.7619053,
}

PROPS = [KILLINGSWORTH, AUSTIN]


def listing(**kw):
    base = {
        "title": "",
        "description": "",
        "location": "",
        "street_address": None,
        "latitude": None,
        "longitude": None,
    }
    base.update(kw)
    return base


CASES = [
    # (name, listing, expected matched property id or None)
    ("geo exact",
     listing(latitude=45.5628691, longitude=-122.6180357), "prop-1"),
    ("geo 80m",
     listing(latitude=45.5635891, longitude=-122.6180357), "prop-1"),
    ("geo 900m reject",
     listing(latitude=45.5709691, longitude=-122.6180357), None),
    ("street_address field",
     listing(street_address="4411 NE Killingsworth St, Portland, OR"), "prop-1"),
    ("address in body only",
     listing(description="Lovely unit at 4411 NE Killingsworth St, move in now"),
     "prop-1"),
    ("address in title only",
     listing(title="4411 NE Killingsworth St - 3BR"), "prop-1"),
    ("same street diff number reject",
     listing(street_address="5522 NE Killingsworth St, Portland, OR"), None),
    ("same number diff street reject",
     listing(street_address="4411 NE Broadway, Portland, OR"), None),
    ("city only reject",
     listing(location="Portland, OR"), None),
    ("stray number reject",
     listing(description="Spacious 4411 sqft home on Alberta"), None),

    # --- D: bare ZIP must never match ---
    ("bare ZIP reject (roommate post)",
     listing(title="3 Beds 1 Bath House", location="Portland, OR, 97218",
             description="We are two friends seeking a housemate for $600."),
     None),
    ("bare ZIP + city reject",
     listing(location="Portland, OR 97218"), None),

    # --- A: ZIP+4 signal ---
    ("zip+4 exact match",
     listing(location="Portland, OR, 97218-1234"), "prop-1"),
    ("zip+4 different plus4 reject",
     listing(location="Portland, OR, 97218-9999"), None),
    ("zip+4 in street_address field",
     listing(street_address="Portland, OR, 97218-1234"), "prop-1"),
    ("zip+4 does not leak to property without one",
     listing(location="Austin, TX, 78701-5555"), None),
    ("zip+4 loses to contradicting geo",
     listing(location="Portland, OR, 97218-1234",
             latitude=45.5628691, longitude=-122.6180357), "prop-1"),
]


def main():
    failures = 0
    for name, lst, expected in CASES:
        match = _find_best_address_match(lst, PROPS)
        got = match["id"] if match else None
        ok = got == expected
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL':4s}  {name:38s} expected={expected} got={got}")
    print()
    print(f"{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""AI fraud detection service for rental listings."""

import hashlib
import inspect
import logging
import os
import re
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from geocode import GEO_MATCH_RADIUS_METRES, haversine_metres

logger = logging.getLogger(__name__)

# Model preference: primary then fallback
_PRIMARY_MODEL = "gemini-3.5-flash"
_FALLBACK_MODEL = "gemini-3.1-flash-lite"


class FraudAnalysisResult(BaseModel):
    """Structured output schema for Gemini fraud analysis."""

    fraud_status: str = Field(
        description='Either "fraud" or "legitimate"',
        pattern="^(fraud|legitimate)$",
    )
    confidence: float = Field(
        description="Confidence score between 0.0 and 1.0",
        ge=0.0,
        le=1.0,
    )
    reason: str = Field(
        description="Detailed explanation of the fraud assessment",
    )


def _normalize_address(addr: str) -> str:
    """Normalize an address string for fuzzy comparison.

    Strips punctuation, lowercases, and collapses whitespace.
    """
    if not addr:
        return ""
    # Lowercase
    normalized = addr.lower()
    # Remove common punctuation and special chars, keep alphanumerics and spaces
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    # Collapse multiple spaces
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_street_number(addr: str) -> Optional[str]:
    """Extract the leading street number from an address."""
    match = re.search(r"^\s*(\d+[a-zA-Z]?)", addr)
    if match:
        return match.group(1).lower()
    return None


def _build_full_property_address(prop: dict) -> str:
    """Build a full address string from a property dict."""
    parts = [
        prop.get("address", ""),
        prop.get("city", ""),
        prop.get("state", ""),
        prop.get("zip_code", ""),
    ]
    return ", ".join(p for p in parts if p)


# Generic address tokens that carry no identity: directions, street types,
# unit indicators. City/state tokens are added dynamically per property.
_ADDRESS_STOPWORDS = {
    "n", "s", "e", "w", "ne", "nw", "se", "sw",
    "north", "south", "east", "west",
    "st", "street", "str", "ave", "avenue", "av", "blvd", "boulevard",
    "rd", "road", "ct", "court", "dr", "drive", "ln", "lane", "pl",
    "place", "ter", "terrace", "way", "cir", "circle", "hwy", "highway",
    "unit", "apt", "apartment", "suite", "ste", "no", "num", "number",
    "fl", "floor", "po", "box", "usa", "us",
}


def _meaningful_tokens(addr: str, extra_stop: set[str]) -> set[str]:
    """Tokens of an address that actually identify it (no generic words)."""
    tokens = set(_normalize_address(addr).split())
    return {t for t in tokens if t not in _ADDRESS_STOPWORDS and t not in extra_stop}


_ZIP5_RE = re.compile(r"^\d{5}$")
_ZIP_PLUS4_RE = re.compile(r"\b(\d{5})-(\d{4})\b")


def _is_zip_token(token: str) -> bool:
    """A bare 5-digit ZIP. Shared by every listing in the neighbourhood, so it
    can never on its own establish that two addresses are the same place."""
    return bool(_ZIP5_RE.match(token))


def _extract_zip_plus4(*texts: str) -> Optional[str]:
    """Pull a normalized 9-digit ZIP+4 out of the first text that has one."""
    for text in texts:
        if not text:
            continue
        match = _ZIP_PLUS4_RE.search(str(text))
        if match:
            return match.group(1) + match.group(2)
    return None


def _zip_plus4_match(listing: dict, prop: dict) -> Optional[str]:
    """Exact ZIP+4 agreement between a listing and a property.

    A ZIP+4 covers roughly one block face or a single building, so agreement
    puts the listing at the property's front door — far tighter than the
    5-digit ZIP, though not proof of the same unit.
    """
    prop_zip4 = _extract_zip_plus4(prop.get("zip_plus4") or "")
    if not prop_zip4:
        return None
    listing_zip4 = _extract_zip_plus4(
        listing.get("street_address") or "",
        listing.get("location") or "",
    )
    if listing_zip4 and listing_zip4 == prop_zip4:
        return f"{prop_zip4[:5]}-{prop_zip4[5:]}"
    return None


def _address_match_score(
    listing_location: str, property_address: str, extra_stop: set[str] | None = None
) -> float:
    """Compute a simple address match score between 0 and 1.

    Checks street number + street name overlap, ignoring generic tokens
    (street types, directions, city/state names) so that "Portland" or
    "4411 Broadway, Portland, OR" does not match a Killingsworth property.
    """
    extra_stop = extra_stop or set()
    listing_norm = _normalize_address(listing_location)
    prop_norm = _normalize_address(property_address)

    if not listing_norm or not prop_norm:
        return 0.0

    # Street numbers, computed up front — they gate both the substring
    # shortcut and the token-overlap bonus below.
    listing_num = _extract_street_number(listing_location)
    prop_num = _extract_street_number(property_address)
    numbers_agree = bool(listing_num and prop_num and listing_num == prop_num)

    # Direct substring check (one contained in the other). Requires the street
    # numbers to agree, otherwise a listing carrying only "Portland, OR, 97218"
    # is a literal substring of every property address in that ZIP and scores a
    # perfect 1.0. That exact bug matched a $600 roommate-wanted post to a
    # registered townhome.
    min_len = 15
    if numbers_agree and len(listing_norm) >= min_len and len(prop_norm) >= min_len:
        if listing_norm in prop_norm or prop_norm in listing_norm:
            return 1.0

    # Token overlap on meaningful tokens only
    listing_tokens = _meaningful_tokens(listing_norm, extra_stop)
    prop_tokens = _meaningful_tokens(prop_norm, extra_stop)
    if not listing_tokens or not prop_tokens:
        return 0.0

    intersection = listing_tokens & prop_tokens
    union = listing_tokens | prop_tokens
    jaccard = len(intersection) / len(union)

    # Street number match bonus — only when the street number matches AND at
    # least one meaningful non-numeric token (street name) is shared, so
    # "4411 Killingsworth" matches but "4411 Broadway" does not.
    if numbers_agree:
        if any(not t.isdigit() for t in intersection - {listing_num}):
            jaccard = max(jaccard, 0.6)

    # Overlap consisting of nothing but 5-digit ZIPs is neighbourhood-level
    # evidence, never identity. Cap it below the match threshold so it can
    # narrow candidates but never declare a match on its own.
    if intersection and not any(not _is_zip_token(t) for t in intersection):
        return min(jaccard, 0.3)

    return jaccard


def _text_contains_address(text: str, prop: dict, extra_stop: set[str]) -> bool:
    """Does free text mention this property's street address?

    Used against listing titles and bodies, where a Jaccard score would be
    meaningless because the text is long. Requires BOTH the street number as a
    standalone token AND a distinctive street-name token, so "4411" alone or
    "Killingsworth" alone (a mile-long street) will not trigger.
    """
    if not text:
        return False
    normalized = _normalize_address(text)
    if not normalized:
        return False

    prop_addr = str(prop.get("address", "") or "")
    number = _extract_street_number(prop_addr)
    if not number:
        return False

    tokens = set(normalized.split())
    if number not in tokens:
        return False

    name_tokens = {
        t for t in _meaningful_tokens(prop_addr, extra_stop) if not t.isdigit()
    }
    return bool(name_tokens & tokens)


def _geo_distance_metres(listing: dict, prop: dict) -> Optional[float]:
    """Distance between a listing and a property, when both have coordinates."""
    try:
        lat1, lon1 = listing.get("latitude"), listing.get("longitude")
        lat2, lon2 = prop.get("latitude"), prop.get("longitude")
        if None in (lat1, lon1, lat2, lon2):
            return None
        return haversine_metres(float(lat1), float(lon1), float(lat2), float(lon2))
    except (TypeError, ValueError):
        return None


def _find_best_address_match(listing: dict, properties: list[dict]) -> Optional[dict]:
    """Find the property this listing refers to, if any.

    Thin wrapper over `_match_with_signal` that returns just the property, for
    callers (and the regression suite) that don't care which signal fired.
    """
    return _match_with_signal(listing, properties)[0]


def _match_with_signal(
    listing: dict, properties: list[dict]
) -> tuple[Optional[dict], str, float]:
    """Find the property this listing refers to, plus *why* it matched.

    Returns (property_or_None, signal_description, score). The signal is
    recorded on the resulting case: when we eventually put a takedown notice in
    front of a human to sign, "matched via geo (12m)" and "matched via zip+4"
    are very different levels of evidence and they must be able to see which.

    Four independent signals, strongest first:
      1. Geo proximity (1.0) — listing coordinates within
         GEO_MATCH_RADIUS_METRES of the property. Robust to any spelling.
      2. Address in title/body (0.9) — how a scammer who leaves the address
         field blank still gives themselves away.
      3. Address-field text score (0..1) — against the enriched
         `street_address` from the detail page, falling back to `location`.
      4. ZIP+4 agreement (0.7) — a ZIP+4 covers about one block face or one
         building. This is the only geo hook available on Facebook, which
         publishes ZIP+4 for ~80% of listings but never a street address.

    A shared 5-digit ZIP is deliberately NOT a signal — it is
    neighbourhood-level and capped below the 0.5 threshold.
    """
    best_match: Optional[dict] = None
    best_score = 0.0
    best_signal = ""

    address_text = listing.get("street_address") or listing.get("location") or ""
    body_text = " ".join(
        str(listing.get(field) or "") for field in ("title", "description")
    )

    for prop in properties:
        prop_full_addr = _build_full_property_address(prop)
        # Treat the property's own city/state tokens as generic — every
        # listing in the same city shares them, so they must not count
        # toward a match.
        extra_stop = {
            _normalize_address(str(prop.get("city", "") or "")),
            _normalize_address(str(prop.get("state", "") or "")),
        }
        extra_stop.discard("")

        distance = _geo_distance_metres(listing, prop)
        if distance is not None and distance <= GEO_MATCH_RADIUS_METRES:
            score, signal = 1.0, f"geo ({distance:.0f}m)"
        else:
            score = _address_match_score(address_text, prop_full_addr, extra_stop)
            signal = "address field"
            if score < 0.9 and _text_contains_address(body_text, prop, extra_stop):
                score, signal = 0.9, "address in title/body"
            if score < 0.7:
                zip4 = _zip_plus4_match(listing, prop)
                if zip4:
                    score, signal = 0.7, f"zip+4 ({zip4})"

        if score > best_score:
            best_score, best_match, best_signal = score, prop, signal

    if best_match and best_score >= 0.5:
        logger.info(
            "Address match found via %s: score=%.2f listing='%s' property='%s'",
            best_signal,
            best_score,
            (address_text or listing.get("title", ""))[:70],
            _build_full_property_address(best_match),
        )
        return best_match, best_signal, best_score

    logger.info(
        "No address match for listing='%s' (best score=%.2f)",
        (address_text or listing.get("title", ""))[:70],
        best_score,
    )
    return None, "", best_score


def _get_gemini_client() -> genai.Client:
    """Create a Gemini client from environment variables."""
    api_key = os.environ.get("GEMINI_WORKSHOP_API_KEY") or os.environ.get("GEMINI_API_KEY")
    base_url = os.environ.get("GEMINI_WORKSHOP_BASE_URL")

    http_options = None
    if base_url:
        http_options = {"base_url": base_url}

    return genai.Client(api_key=api_key, http_options=http_options)


_FEW_SHOT_EXAMPLES = """
## REFERENCE EXAMPLES (calibration — these are NOT the listing under review)

### Example A — FRAUD (confidence ~1.0)
Real property: 4411 NE Killingsworth St Unit 107, Portland OR, 3bd/1.5ba, $2995/mo.
Listing: "URGENT: Beautiful 3BR townhouse for rent", $1200/mo, body reads
"Owner moving abroad for missionary work, must fill unit immediately. First month +
deposit via Zelle or CashApp to hold. No credit check, no viewing needed - keys shipped
after payment. text only 555-123-4567".
Why fraud: same address as a monitored property, but rent cut ~60% below the real
$2995; absentee-owner story; irreversible peer-to-peer payment demanded up front;
sight-unseen rental with keys "shipped"; no screening; off-platform text-only contact.
Any ONE of the last four is a strong signal — together they are conclusive.

### Example B — LEGITIMATE (confidence ~0.95)
Same real property. Listing quotes the same address, $2995/mo, 3bd/1.5ba, and
describes the fenced backyard, granite counters, hardwood floors and designated
parking that the property record lists.
Why legitimate: price matches, unit details match, amenities match the property
record, no payment-pressure or absentee-owner language. A listing that accurately
mirrors a monitored property is the property's OWN advertisement, not a scam.

Key calibration rule: an address match alone is NOT fraud — your own listings will
match. Fraud requires an address match PLUS at least one concrete anomaly (deep price
cut, payment/urgency red flags, or contradicted unit details).
"""


def _build_analysis_prompt(listing: dict, matched_property: dict) -> str:
    """Build the Gemini prompt for fraud analysis."""
    prop_images = matched_property.get("image_urls", [])
    if isinstance(prop_images, str):
        prop_images = prop_images.split(",") if prop_images else []

    listing_images = listing.get("image_urls", [])
    if isinstance(listing_images, str):
        listing_images = listing_images.split(",") if listing_images else []

    prompt = f"""You are a rental fraud detection expert. Analyze whether the scraped listing is a fraudulent copy of the real property owned by the management firm.

## REAL PROPERTY (owned by management firm)
- ID: {matched_property.get("id", "")}
- Name: {matched_property.get("name", "")}
- Address: {_build_full_property_address(matched_property)}
- Bedrooms: {matched_property.get("bedrooms", "N/A")}
- Bathrooms: {matched_property.get("bathrooms", "N/A")}
- Square Footage: {matched_property.get("square_footage", "N/A")}
- Monthly Rent: ${matched_property.get("monthly_rent", "N/A")}
- Amenities: {", ".join(matched_property.get("amenities", [])) if matched_property.get("amenities") else "N/A"}
- Description: {matched_property.get("description", "N/A")}
- Property Images: {len(prop_images)} image(s)

## SCRAPED LISTING (from {listing.get("source", "unknown")})
- Title: {listing.get("title", "")}
- Price: ${listing.get("price", "N/A")}
- Location: {listing.get("location", "N/A")}
- Description: {listing.get("description", "N/A")}
- Listing Images: {len(listing_images)} image(s)
- URL: {listing.get("url", "N/A")}

## ANALYSIS INSTRUCTIONS
Check for the following fraud indicators:
1. **Price disparity**: Is the listing price significantly lower than the real property's rent? (e.g., 30%+ below market rate)
2. **Different contact info**: Does the listing description mention a different landlord, property manager, or contact method than expected?
3. **Stolen photos**: Does the listing use the same images as the real property? (Note: we cannot visually compare images, but flag if image counts differ drastically or if the listing claims to have photos that don't match the property description)
4. **Mismatched details**: Are bedroom/bathroom counts, square footage, or amenities inconsistent with the real property?
5. **Suspicious language**: Does the description contain urgency tactics, unusual payment requests, or claims the owner is "out of town"?
{_FEW_SHOT_EXAMPLES}
Return your assessment as structured JSON with:
- fraud_status: either "fraud" or "legitimate"
- confidence: a number from 0.0 to 1.0
- reason: a concise explanation of your findings
"""
    return prompt


def _compute_prompt_version() -> str:
    """Short hash identifying the exact prompt that produced a verdict.

    Hashing the source of `_build_analysis_prompt` plus the few-shot block means
    this changes automatically whenever the prompt is edited — nobody has to
    remember to bump a constant. Stored on every Case so that verdicts from
    before and after a prompt change are distinguishable rather than silently
    incomparable.
    """
    try:
        material = inspect.getsource(_build_analysis_prompt) + _FEW_SHOT_EXAMPLES
    except (OSError, TypeError):  # pragma: no cover - source always available in practice
        material = _FEW_SHOT_EXAMPLES
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


_PROMPT_VERSION = _compute_prompt_version()


async def analyze_listing(listing: dict, properties: list[dict]) -> dict:
    """Compare a scraped listing against real properties and return fraud analysis.

    Args:
        listing: A scraped listing dict (matches ScrapedListing.to_dict() schema).
        properties: A list of real property dicts (matches Property.to_dict() schema).

    Returns:
        A dict with keys: fraud_status, confidence, reason, matched_property_id,
        match_signal.
    """
    # 1. Quick address similarity check.
    # A listing that doesn't match any owned property's address is simply
    # unrelated to the monitored portfolio — it is NOT fraud. Fraud means a
    # listing that impersonates one of our properties.
    matched_property, match_signal, _score = _match_with_signal(listing, properties)

    if not matched_property:
        return {
            "fraud_status": "unknown",
            "confidence": 0.0,
            "reason": "No address match — listing is not related to any monitored property",
            "matched_property_id": None,
            "match_signal": None,
            # No model was consulted, so there is no model to attribute this to.
            "model_name": None,
            "prompt_version": _PROMPT_VERSION,
        }

    # 2. Use Gemini to analyze the matched pair
    client = _get_gemini_client()
    prompt = _build_analysis_prompt(listing, matched_property)

    response_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "fraud_status": types.Schema(
                type=types.Type.STRING,
                enum=["fraud", "legitimate"],
            ),
            "confidence": types.Schema(
                type=types.Type.NUMBER,
            ),
            "reason": types.Schema(
                type=types.Type.STRING,
            ),
        },
        required=["fraud_status", "confidence", "reason"],
    )

    config_gemini = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    models_to_try = [_PRIMARY_MODEL, _FALLBACK_MODEL]
    last_error = None

    for model_name in models_to_try:
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config_gemini,
            )

            # Parse structured output
            parsed = response.parsed
            if parsed is None and response.text:
                # Fallback: try to parse text as JSON
                import json

                try:
                    parsed = json.loads(response.text)
                except json.JSONDecodeError:
                    logger.warning("Gemini returned non-JSON text for model %s", model_name)
                    continue

            if parsed is None:
                logger.warning("Gemini returned empty parsed response for model %s", model_name)
                continue

            fraud_status = getattr(parsed, "fraud_status", parsed.get("fraud_status", "fraud"))
            confidence = getattr(parsed, "confidence", parsed.get("confidence", 0.5))
            reason = getattr(parsed, "reason", parsed.get("reason", "No reason provided"))

            # Normalize confidence
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.5

            # Clamp confidence
            confidence = max(0.0, min(1.0, confidence))

            # Normalize status
            if fraud_status not in ("fraud", "legitimate"):
                fraud_status = "fraud" if confidence > 0.5 else "legitimate"

            return {
                "fraud_status": fraud_status,
                "confidence": confidence,
                "reason": reason,
                "matched_property_id": matched_property.get("id"),
                "match_signal": match_signal,
                # Which model actually answered — not which one we asked first.
                # The fallback chain means these differ more often than you'd think.
                "model_name": model_name,
                "prompt_version": _PROMPT_VERSION,
            }

        except Exception as exc:
            logger.warning("Gemini analysis failed for model %s: %s", model_name, exc)
            last_error = exc
            continue

    # All models failed — mark as unknown rather than guessing "fraud"
    logger.error("All Gemini models failed. Last error: %s", last_error)
    return {
        "fraud_status": "unknown",
        "confidence": 0.0,
        "reason": f"AI analysis service unavailable: {last_error}",
        "matched_property_id": matched_property.get("id"),
        "match_signal": match_signal,
        "model_name": None,
        "prompt_version": _PROMPT_VERSION,
    }


# ─── Pasted-listing parsing ─────────────────────────────────────────────────

_PARSE_PROMPT = """You parse raw rental listing text that a user copied from a marketplace website (Craigslist, Facebook Marketplace, Zillow, Apartments.com, etc.) into structured fields.

Extract the following from the pasted text:
- title: the listing headline/title. If none, write a short descriptive title.
- price: the monthly rent as a number (no $, commas, or "/mo"). Null if absent.
- location: the property address if stated, otherwise the neighborhood/city. Empty string if absent.
- description: the listing body / details / amenity list. Empty string if absent.
- url: any URL found in the text. Empty string if absent.
- source: one of "craigslist", "facebook_marketplace", "zillow", "apartments_com", "manual" — best guess from the text; use "manual" if unclear.

## PASTED TEXT
{text}

Return the extraction as structured JSON."""


async def parse_listing_text(text: str) -> dict:
    """Parse raw pasted listing text into structured listing fields via Gemini.

    Returns a dict with keys: title, price, location, description, url, source.
    Falls back to a minimal manual parse (whole text as description) if the
    AI service is unavailable.
    """
    client = _get_gemini_client()
    prompt = _PARSE_PROMPT.format(text=text[:8000])

    response_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(type=types.Type.STRING),
            "price": types.Schema(type=types.Type.NUMBER, nullable=True),
            "location": types.Schema(type=types.Type.STRING),
            "description": types.Schema(type=types.Type.STRING),
            "url": types.Schema(type=types.Type.STRING),
            "source": types.Schema(
                type=types.Type.STRING,
                enum=["craigslist", "facebook_marketplace", "zillow", "apartments_com", "manual"],
            ),
        },
        required=["title", "location", "description", "url", "source"],
    )

    config_gemini = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    for model_name in (_PRIMARY_MODEL, _FALLBACK_MODEL):
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config_gemini,
            )
            parsed = response.parsed
            if parsed is None and response.text:
                import json

                try:
                    parsed = json.loads(response.text)
                except json.JSONDecodeError:
                    continue
            if parsed is None:
                continue

            result = {
                "title": str(getattr(parsed, "title", "") or parsed.get("title", "") or ""),
                "price": getattr(parsed, "price", None) if not isinstance(parsed, dict) else parsed.get("price"),
                "location": str(getattr(parsed, "location", "") or parsed.get("location", "") or ""),
                "description": str(getattr(parsed, "description", "") or parsed.get("description", "") or ""),
                "url": str(getattr(parsed, "url", "") or parsed.get("url", "") or ""),
                "source": str(getattr(parsed, "source", "") or parsed.get("source", "") or "manual"),
            }
            try:
                result["price"] = float(result["price"]) if result["price"] is not None else None
            except (TypeError, ValueError):
                result["price"] = None
            if not result["title"]:
                result["title"] = "Pasted listing"
            return result
        except Exception as exc:
            logger.warning("Gemini parse failed for model %s: %s", model_name, exc)
            continue

    # AI unavailable — degrade gracefully so the paste still imports
    logger.error("All Gemini models failed for pasted-listing parse; using raw text")
    return {
        "title": "Pasted listing",
        "price": None,
        "location": "",
        "description": text[:8000],
        "url": "",
        "source": "manual",
    }

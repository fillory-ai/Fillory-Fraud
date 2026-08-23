"""Twilio SMS notifier for fraud alerts."""
import os
from datetime import datetime, timezone

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from config import (
    TWILIO_ENABLED,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    ALERT_PHONE_NUMBER,
)


def is_twilio_enabled() -> bool:
    """Check whether Twilio SMS sending is enabled.

    Twilio is disabled by default to prevent the Twilio account from seeing
    unexpected deployments. Set TWILIO_ENABLED=true once the app is running
    from its final published URL.
    """
    return TWILIO_ENABLED


def send_fraud_alert(listing: dict, property_name: str) -> dict:
    """Send an SMS alert about a suspected fraudulent listing.

    Args:
        listing: The scraped listing dict with title, url, price, etc.
        property_name: Name of the real property being impersonated.

    Returns:
        dict with status and message
    """
    # ── Safe mode: do not touch the Twilio API unless explicitly enabled ──
    if not is_twilio_enabled():
        return {
            "status": "skipped",
            "error_message": "Twilio SMS disabled (TWILIO_ENABLED is not true). "
                             "Alert recorded but not sent.",
        }

    account_sid = TWILIO_ACCOUNT_SID
    auth_token = TWILIO_AUTH_TOKEN
    from_number = TWILIO_PHONE_NUMBER
    to_number = ALERT_PHONE_NUMBER

    missing = []
    if not account_sid:
        missing.append("TWILIO_ACCOUNT_SID")
    if not auth_token:
        missing.append("TWILIO_AUTH_TOKEN")
    if not from_number:
        missing.append("TWILIO_PHONE_NUMBER")
    if not to_number:
        missing.append("ALERT_PHONE_NUMBER")

    if missing:
        return {
            "status": "failed",
            "error_message": f"Missing Twilio config: {', '.join(missing)}",
        }

    price_str = f"${listing.get('price'):,.0f}" if listing.get("price") else "Unknown price"
    source = listing.get("source", "Unknown").replace("_", " ").title()
    title = listing.get("title", "Unknown listing")
    url = listing.get("url", "")

    message_body = (
        f"🚨 FRAUD ALERT: {property_name}\n\n"
        f"A suspicious listing was found on {source}:\n"
        f"\"{title}\"\n"
        f"Price: {price_str}\n"
        f"View: {url}\n\n"
        f"Investigate immediately."
    )

    try:
        client = TwilioClient(account_sid, auth_token)
        twilio_msg = client.messages.create(
            body=message_body,
            from_=from_number,
            to=to_number,
        )
        return {
            "status": "sent",
            "sid": twilio_msg.sid,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    except TwilioRestException as e:
        return {
            "status": "failed",
            "error_message": str(e),
        }
    except Exception as e:
        return {
            "status": "failed",
            "error_message": str(e),
        }
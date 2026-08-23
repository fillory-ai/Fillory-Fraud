import os

try:
    import keyring
except ImportError:
    keyring = None


def _env_or_keyring(name: str) -> str:
    """Read a config value from the environment, falling back to keyring.

    Workshop injects secrets as env vars in the sandbox/deployments; locally
    they're only available in the OS keyring.
    """
    value = os.environ.get(name, "")
    if value:
        return value
    if keyring is not None:
        for service in ("workshop", "memex"):
            try:
                value = keyring.get_password(service, name) or ""
            except Exception:
                value = ""
            if value:
                break
    return value


# Scraper settings
SCRAPE_CITY = os.environ.get("SCRAPE_CITY", "Portland")
SCRAPE_STATE = os.environ.get("SCRAPE_STATE", "OR")

# Apify
APIFY_API_KEY = _env_or_keyring("APIFY_API_KEY")

# Twilio
TWILIO_ENABLED = os.environ.get("TWILIO_ENABLED", "false").lower() in ("true", "1", "yes")
TWILIO_ACCOUNT_SID = _env_or_keyring("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env_or_keyring("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _env_or_keyring("TWILIO_PHONE_NUMBER")
ALERT_PHONE_NUMBER = _env_or_keyring("ALERT_PHONE_NUMBER")

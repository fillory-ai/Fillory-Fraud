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


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes")


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ── Alerting policy (M1) ────────────────────────────────────────────────────
# Observe mode: detections are recorded and cases are opened, but nothing is
# ever sent. A newly onboarded account runs here until its false-positive rate
# has been eyeballed. Defaults ON — silence is the safe default.
OBSERVE_MODE = _flag("OBSERVE_MODE", True)
# A case alerts once. It may re-alert only if something material changed AND
# this many hours have passed.
ALERT_COOLDOWN_HOURS = _num("ALERT_COOLDOWN_HOURS", 24)
# Hard ceiling. Anything beyond this in a rolling 24h is recorded as suppressed
# rather than sent — a scraper malfunction must never turn into 200 texts.
MAX_ALERTS_PER_DAY = int(_num("MAX_ALERTS_PER_DAY", 10))

# ── Scheduler (M1) ──────────────────────────────────────────────────────────
# Off by default: scans cost Apify credits, and an unattended local dev machine
# should not spend them.
SCHEDULER_ENABLED = _flag("SCHEDULER_ENABLED", False)
SCAN_INTERVAL_HOURS = _num("SCAN_INTERVAL_HOURS", 4)
# Random 0..N minute offset so multiple deployments don't hit Apify together.
SCAN_JITTER_MINUTES = int(_num("SCAN_JITTER_MINUTES", 10))
# A successful scan older than this means we are blind. Pages us, not the user.
SCAN_STALE_HOURS = _num("SCAN_STALE_HOURS", 12)

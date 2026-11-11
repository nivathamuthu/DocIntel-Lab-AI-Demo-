"""Small presentation helpers shared by every page."""
from django import template

register = template.Library()

_PLAIN = (
    ("LLMRateLimitError", "The model provider is limiting requests right now. Wait a minute, then try again."),
    ("daily quota", "The model provider's daily quota is used up. Try again tomorrow or switch model."),
    ("LLMValidationError", "The model's answer did not match the expected format, even after retries."),
    ("ImproperlyConfigured", "The app is not configured for this provider. Check LLM_PROVIDER and the API key in .env."),
    ("TimeoutExpired", "The step took too long and was stopped."),
    ("OperationalError", "The database or a service was not reachable. Check that the containers are running."),
    ("ConnectionError", "A service was not reachable. Check that the containers are running."),
    ("No free LLM slot", "Too many model calls were already running. Try again in a moment."),
)


@register.filter
def plain_error(error: str) -> str:
    """A short sentence for a stored error string. The original text stays available for details."""
    text = (error or "").strip()
    if not text:
        return ""
    for needle, sentence in _PLAIN:
        if needle.lower() in text.lower():
            return sentence
    first = text.splitlines()[0]
    if ": " in first and first.split(": ", 1)[0].replace("_", "").isalnum():
        first = first.split(": ", 1)[1]
    return f"Something went wrong: {first[:200]}"


@register.filter
def percent(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


@register.filter
def meter_class(value) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "low"
    if score >= 0.8:
        return ""
    return "mid" if score >= 0.5 else "low"


@register.filter
def get_item(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None

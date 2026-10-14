"""Server-mode limits, enforced below the UI so they cannot be bypassed."""
from __future__ import annotations

from datetime import date

#: Largest to smallest; anything above `max_preset` in this order is refused.
_PRESET_ORDER = ["4k", "1440p", "1080p", "720p"]
#: Every value the GUI offers (gui.PRESET_CHOICES), plus blank. Anything else is refused,
#: because a request need not come from the dropdown.
_NATIVE = "native (no upscaling)"
_ALLOWED_PRESETS = (*_PRESET_ORDER, _NATIVE, "")


class CapExceeded(ValueError):
    """A submitted run asks for more than server mode allows."""


def check(params: dict, max_years: int = 12, max_preset: str = "1440p") -> None:
    try:
        start = date.fromisoformat(str(params["start"]))
        end = date.fromisoformat(str(params["end"]))
    except (KeyError, ValueError) as exc:
        raise CapExceeded(f"Could not read the date range: {exc}") from exc
    if end <= start:
        raise CapExceeded(
            f"The end date {end.isoformat()} is not after the start date {start.isoformat()}; "
            "a run needs an end date after its start date.")
    days = (end - start).days
    if days / 365.25 > max_years:
        # Whole days on both sides: a "12.0 years" figure next to a "12 years" limit
        # reads as a contradiction when the range is a few days over.
        raise CapExceeded(
            f"That range is {days} days; server mode allows {max_years} years "
            f"({int(max_years * 365.25)} days) per render. Split it into two runs.")
    preset = str(params.get("preset") or "")
    if preset not in _ALLOWED_PRESETS:
        raise CapExceeded(
            f"Unknown preset {preset!r}; allowed presets are 4k, 1440p, 1080p, 720p, "
            "native (no upscaling), or blank.")
    if preset in _PRESET_ORDER and _PRESET_ORDER.index(preset) < _PRESET_ORDER.index(max_preset):
        raise CapExceeded(
            f"{preset} is larger than server mode allows; the maximum is {max_preset}.")

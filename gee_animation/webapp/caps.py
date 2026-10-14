"""Server-mode limits, enforced below the UI so they cannot be bypassed."""
from __future__ import annotations

from datetime import date

#: Largest to smallest; anything above `max_preset` in this order is refused.
_PRESET_ORDER = ["4k", "1440p", "1080p", "720p"]


class CapExceeded(ValueError):
    """A submitted run asks for more than server mode allows."""


def check(params: dict, max_years: int = 12, max_preset: str = "1440p") -> None:
    try:
        start = date.fromisoformat(str(params["start"]))
        end = date.fromisoformat(str(params["end"]))
    except (KeyError, ValueError) as exc:
        raise CapExceeded(f"Could not read the date range: {exc}") from exc
    years = (end - start).days / 365.25
    if years > max_years:
        raise CapExceeded(
            f"That range is {years:.1f} years; server mode allows {max_years} years "
            "per render. Split it into two runs.")
    preset = str(params.get("preset") or "")
    if preset in _PRESET_ORDER and _PRESET_ORDER.index(preset) < _PRESET_ORDER.index(max_preset):
        raise CapExceeded(
            f"{preset} is larger than server mode allows; the maximum is {max_preset}.")

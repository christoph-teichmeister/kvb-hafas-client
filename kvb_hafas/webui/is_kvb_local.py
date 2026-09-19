"""Python port of the source repo's `map.html` `cssClass(category)` JS helper.

Buckets a raw HAFAS `catOut` string ("Str", "Bus", "S-Bahn", "RE", ...) into
local KVB modes (tram/Stadtbahn, bus) vs. everything else (S-Bahn, regional
and long-distance rail, which merely pass through KVB's bounding box).
"""

from __future__ import annotations


def css_class(category: str | None) -> str:
    """Bucket a raw HAFAS category into 'tram' / 'bus' / 'rail'.

    Mirrors map.html's cssClass(): substring match on lowercased category,
    "str"/"tram"/"stadtbahn" -> tram, "bus" -> bus, anything else -> rail.
    """
    c = (category or "").lower()
    if "str" in c or "tram" in c or "stadtbahn" in c:
        return "tram"
    if "bus" in c:
        return "bus"
    return "rail"


def is_kvb_local(category: str | None) -> bool:
    """True for KVB's own modes (tram/Stadtbahn, bus), False for foreign rail
    (S-Bahn, RE, IC, ICE, ...) that merely passes through the bbox."""
    return css_class(category) in ("tram", "bus")


def is_kvb_tram(category: str | None) -> bool:
    """True only for KVB tram/Stadtbahn vehicles, excluding bus and rail."""
    return css_class(category) == "tram"

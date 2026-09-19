"""Live snapshot stats (from the currently cached /api/vehicles payload, no DB
access, no extra KVB request) and historical aggregations (delegated to
history_store.HistoryStore, which does the actual SQL).
"""

from __future__ import annotations

from typing import Any

from kvb_hafas.webui.is_kvb_local import css_class


def live_stats(vehicles: list[dict[str, Any]], late_from: int = 3, mode: str | None = None) -> dict[str, Any]:
    """Compute live stats straight from an already-fetched vehicle list.

    - max current delay across vehicles with realtime data, plus which vehicle
    - vehicle count per line (which line currently has the most vehicles out), plus that count
    - count of currently-delayed vehicles (delay >= late_from)

    `mode` ("tram"/"bus"/"rail"), when given, restricts the vehicle list to
    that mode (via css_class) before any aggregation, so every returned
    number is scoped to it.
    """
    if mode:
        vehicles = [v for v in vehicles if css_class(v.get("category")) == mode]

    per_line: dict[str, int] = {}
    delays_with_vehicle: list[tuple[int, dict[str, Any]]] = []

    for v in vehicles:
        line = v.get("line") or "?"
        per_line[line] = per_line.get(line, 0) + 1
        if v.get("delay") is not None:
            delays_with_vehicle.append((v["delay"], v))

    delayed_count = sum(1 for d, _ in delays_with_vehicle if d >= late_from)

    busiest_line: str | None = None
    busiest_line_count = 0
    if per_line:
        busiest_line, busiest_line_count = max(per_line.items(), key=lambda kv: kv[1])

    max_delay_entry = max(delays_with_vehicle, key=lambda t: t[0], default=None)
    max_delay_vehicle = None
    if max_delay_entry is not None:
        _, veh = max_delay_entry
        max_delay_vehicle = {
            "jid": veh.get("jid"),
            "line": veh.get("line"),
            "direction": veh.get("direction"),
        }

    return {
        "vehicle_count": len(vehicles),
        "vehicles_per_line": dict(sorted(per_line.items(), key=lambda kv: -kv[1])),
        "busiest_line": busiest_line,
        "busiest_line_count": busiest_line_count,
        "max_current_delay_minutes": max_delay_entry[0] if max_delay_entry else None,
        "max_current_delay_vehicle": max_delay_vehicle,
        "delayed_vehicle_count": delayed_count,
        "delayed_threshold_minutes": late_from,
    }

"""Live snapshot stats (from the currently cached /api/vehicles payload, no DB
access, no extra KVB request) and historical aggregations (delegated to
history_store.HistoryStore, which does the actual SQL).
"""

from __future__ import annotations

from typing import Any

from kvb_hafas.webui.is_kvb_local import css_class, is_kvb_local


def live_stats(vehicles: list[dict[str, Any]], late_from: int = 3) -> dict[str, Any]:
    """Compute live stats straight from an already-fetched vehicle list.

    - max current delay across vehicles with realtime data
    - vehicle count per line (which line currently has the most vehicles out)
    - count of currently-delayed vehicles (delay >= late_from)
    - vehicle count per mode (tram/bus/rail)
    """
    per_line: dict[str, int] = {}
    per_mode: dict[str, int] = {"tram": 0, "bus": 0, "rail": 0}
    delays = [v["delay"] for v in vehicles if v.get("delay") is not None]
    delayed_count = sum(1 for d in delays if d >= late_from)

    for v in vehicles:
        line = v.get("line") or "?"
        per_line[line] = per_line.get(line, 0) + 1
        mode = css_class(v.get("category"))
        per_mode[mode] = per_mode.get(mode, 0) + 1

    busiest_line = max(per_line.items(), key=lambda kv: kv[1])[0] if per_line else None

    return {
        "vehicle_count": len(vehicles),
        "vehicles_per_line": dict(sorted(per_line.items(), key=lambda kv: -kv[1])),
        "vehicles_per_mode": per_mode,
        "busiest_line": busiest_line,
        "max_current_delay_minutes": max(delays) if delays else None,
        "delayed_vehicle_count": delayed_count,
        "delayed_threshold_minutes": late_from,
        "vehicles_with_realtime": len(delays),
        "local_vehicle_count": sum(1 for v in vehicles if is_kvb_local(v.get("category"))),
    }

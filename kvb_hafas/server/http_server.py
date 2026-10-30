"""HTTP server for the live map/departures/dashboard web UI.

Vehicle/alert/network/stops endpoints, background geometry prefetch, SQLite
vehicle history (history_store.py), live + historical stats (stats.py) and a
stop-search/departure-board API. Serves `kvb_hafas.webui`'s bundled HTML/CSS/
Leaflet assets via importlib.resources.

Runs standalone: `uv run map_server.py` (repo root) with no Home Assistant
around it. The `kvb-ha-map` HA add-on imports this module directly
(`python -m kvb_hafas.server.http_server`) instead of carrying its own copy,
reading the same env vars from its `run.sh`/`config.yaml`.

Ingress: HA Supervisor proxies requests to that add-on with a dynamic base
path (X-Ingress-Path header). Rather than rewrite that path into every served
HTML/JS response, every static asset URL and API call the frontend makes is
RELATIVE (no leading slash) — the browser resolves those against the current
page URL, which already includes the ingress base path, so this server never
needs to know or care what that base path is. We still read and expose the
header (see /api/config's "ingress_path") for any frontend code that wants it
(e.g. debugging); outside of HA it's simply always empty.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kvb_hafas import KVBHafasClient, KVBHafasError
from kvb_hafas.parsing import _delay_minutes, station_ext_id
from kvb_hafas.server.eta import journey_eta
from kvb_hafas.server.history_store import DAY_TYPES, HistoryStore
from kvb_hafas.server.stats import live_stats
from kvb_hafas.webui import is_kvb_local


# --------------------------------------------------------------------------
# Config — env vars first (HA add-on options, exported by run.sh), then the
# same defaults the source prototype and config.yaml agree on. This makes the
# server runnable standalone (no HA, no run.sh) for local dev.
# --------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    value = default
    if raw is not None and raw != "":
        try:
            value = int(raw)
        except ValueError:
            value = default
    # A too-small interval/TTL from the add-on options would hammer KVB or
    # spin a background thread — clamp instead of trusting it.
    return value if minimum is None else max(minimum, value)


def _env_float(name: str, default: float | None, minimum: float | None = None) -> float | None:
    raw = os.environ.get(name)
    value = default
    if raw is not None and raw != "":
        try:
            value = float(raw)
        except ValueError:
            value = default
    if value is not None and value != value:  # NaN
        value = default
    if value is None or minimum is None:
        return value
    return max(minimum, value)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except ValueError:
        pass
    return [s.strip() for s in raw.split(",") if s.strip()]


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8099)

HISTORY_ENABLED = _env_bool("HISTORY_ENABLED", True)
HISTORY_SAMPLE_INTERVAL_SECONDS = _env_int("HISTORY_SAMPLE_INTERVAL_SECONDS", 60, minimum=10)
HISTORY_RETENTION_DAYS = _env_int("HISTORY_RETENTION_DAYS", 365, minimum=1)
HISTORY_FILTER_LOCAL_ONLY = _env_bool("HISTORY_FILTER_LOCAL_ONLY", True)
VEHICLE_POLL_CACHE_TTL_SECONDS = _env_float("VEHICLE_POLL_CACHE_TTL_SECONDS", 15.0, minimum=5.0)
ALERT_CACHE_TTL_SECONDS = _env_float("ALERT_CACHE_TTL_SECONDS", 300.0, minimum=30.0)
DEFAULT_MAP_CENTER_LAT = _env_float("DEFAULT_MAP_CENTER_LAT", 50.9375)
DEFAULT_MAP_CENTER_LON = _env_float("DEFAULT_MAP_CENTER_LON", 6.9603)
DEFAULT_ZOOM = _env_int("DEFAULT_ZOOM", 13)
# City-wide box the background vehicle-snapshot poller fetches on its own
# timer, so /api/stats/live and the history sampler have fresh data even
# when no map.html tab is open polling its own (viewport-sized) bbox.
VEHICLE_SNAPSHOT_MIN_LAT = _env_float("VEHICLE_SNAPSHOT_MIN_LAT", 50.80)
VEHICLE_SNAPSHOT_MIN_LON = _env_float("VEHICLE_SNAPSHOT_MIN_LON", 6.75)
VEHICLE_SNAPSHOT_MAX_LAT = _env_float("VEHICLE_SNAPSHOT_MAX_LAT", 51.05)
VEHICLE_SNAPSHOT_MAX_LON = _env_float("VEHICLE_SNAPSHOT_MAX_LON", 7.15)
FAVORITE_STOP_IDS = _env_list("FAVORITE_STOP_IDS", [])
DASHBOARD_REFRESH_SECONDS = _env_int("DASHBOARD_REFRESH_SECONDS", 15, minimum=5)
TILE_URL = os.environ.get("TILE_URL") or ""
TILE_ATTRIBUTION = os.environ.get("TILE_ATTRIBUTION") or ""

WEBUI = resources.files("kvb_hafas.webui")
# Relative to CWD by default — `uv run map_server.py` from the repo root finds
# `data/rail_geometry.json` there; kvb-ha-map's run.sh sets DATA_DIR=/app/data
# explicitly, so this only matters for local dev.
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
GEOMETRY_FILE = DATA_DIR / "map_geometry.json"
RAIL_FILE = DATA_DIR / "rail_geometry.json"
# Home Assistant Supervisor mounts the add-on's persistent (and backed-up)
# storage at /data, next to options.json. DATA_DIR in kvb-ha-map is /app/data
# inside the image, which is replaced on every add-on update — so the history
# DB goes to /data whenever we're running as an add-on.
ADDON_DATA_DIR = Path("/data")


def _history_db_file(data_dir: Path, addon_data_dir: Path = ADDON_DATA_DIR) -> Path:
    override = os.environ.get("HISTORY_DB_FILE")
    if override:
        return Path(override)
    if (addon_data_dir / "options.json").is_file():
        return addon_data_dir / "history.db"
    return data_dir / "history.db"


DB_FILE = _history_db_file(DATA_DIR)

TRACK_SECONDS = 120
PREFETCH_CHUNK = 10
MAX_VEHICLES = 600
ALERT_AD_CATEGORY = 99
LATE_FROM = 3
PURGE_INTERVAL_SECONDS = 3600 * 6  # purge check four times a day is plenty for a daily-granularity retention
# First purge shortly after start, so a lowered HISTORY_RETENTION_DAYS (or a
# server that restarts more often than every 6 h) still gets purged.
PURGE_STARTUP_DELAY_SECONDS = 60

# Bounded caches: every distinct viewport bbox / stats parameter set is a key,
# so without a cap a long-running add-on would grow these forever.
VEHICLE_CACHE_MAX_ENTRIES = 64
STATS_CACHE_MAX_ENTRIES = 64

# Background-thread error backoff: doubles per consecutive failure, capped.
ERROR_BACKOFF_CAP_SECONDS = 300.0
PREFETCH_MAX_ATTEMPTS = 3

# Without a browser asking for live data for this long, the snapshot poller
# only runs as often as the history sampler needs it (or rarely, if history
# is off) instead of every VEHICLE_POLL_CACHE_TTL_SECONDS.
VIEWER_IDLE_SECONDS = 300.0
IDLE_POLL_SECONDS = 600.0

# Query-parameter bounds.
MAX_STOP_SEARCH_RESULTS = 20
MAX_DEPARTURES = 50
TS_MIN = 0
TS_MAX = 4102444800  # 2100-01-01; also keeps SQLite's int64 binding safe
HISTORY_VEHICLES_DEFAULT_WINDOW_SECONDS = 3600
HISTORY_VEHICLES_MAX_WINDOW_SECONDS = 24 * 3600

# Dashboard punctuality-trend chart presets: window_seconds, bucket_seconds.
# Server-picked (not client-supplied bucket sizes) to keep the query cheap
# and bounded.
PUNCTUALITY_WINDOWS = {
    "7d": (7 * 86400, 86400),
    "24h": (24 * 3600, 3600),
    "1h": (3600, 300),
}

# Hour-of-day heatmap windows (seconds). Bounded server-side like the above.
PUNCTUALITY_HOUR_WINDOWS = {
    "7d": 7 * 86400,
    "28d": 28 * 86400,
}

# --------------------------------------------------------------------------
# Shared state — same ponytail as the source prototype: one lock, dict caches.
# Fine for a single-user local add-on; not meant to scale past a handful of
# browser tabs.
# --------------------------------------------------------------------------
_client = KVBHafasClient(min_interval=1.0)
_lock = threading.Lock()
_cache: dict[tuple[float, ...], tuple[float, dict]] = {}
_alerts: tuple[float, list[dict]] | None = None
_stats_cache: dict[tuple, tuple[float, dict, float]] = {}

# Stats queries scan the history DB and the dashboard re-polls them; the
# picture barely moves within these windows.
STATS_CACHE_TTL_SECONDS = _env_float("STATS_CACHE_TTL_SECONDS", 60.0, minimum=5.0)
# One JourneyDetails per watched trip and window — the map re-asks every 30 s
# while an ETA panel is open, so this caps KVB traffic at that rate.
JOURNEY_CACHE_TTL_SECONDS = _env_float("JOURNEY_CACHE_TTL_SECONDS", 30.0, minimum=10.0)
_journeys: dict[str, tuple[float, tuple]] = {}
HEATMAP_CACHE_TTL_SECONDS = _env_float("HEATMAP_CACHE_TTL_SECONDS", 300.0, minimum=5.0)

# The history sampler only takes snapshots younger than this: with nobody
# watching, the poller runs every max(TTL, sample interval), so allow two of
# those before treating the snapshot as stale (KVB down, poller stuck).
SNAPSHOT_MAX_AGE_SECONDS = 2 * max(VEHICLE_POLL_CACHE_TTL_SECONDS, HISTORY_SAMPLE_INTERVAL_SECONDS)


def _prune_cache(cache: dict, now: float, ttl: float, max_entries: int) -> None:
    """Drop expired entries, then the oldest ones until there is room for one
    more. Entries are tuples whose first item is their monotonic timestamp;
    an optional third item overrides `ttl` for that entry. Caller holds _lock."""
    for key in [k for k, entry in cache.items() if now - entry[0] >= (entry[2] if len(entry) > 2 else ttl)]:
        del cache[key]
    while len(cache) >= max_entries:
        del cache[min(cache, key=lambda k: cache[k][0])]


def _stats_cached(key: tuple, ttl: float, produce) -> dict:
    now = time.monotonic()
    with _lock:
        hit = _stats_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = produce()
    with _lock:
        _stats_cache.pop(key, None)
        _prune_cache(_stats_cache, now, ttl, STATS_CACHE_MAX_ENTRIES)
        _stats_cache[key] = (now, value, ttl)
    return value


_segments: dict[tuple[str, str], list[tuple[float, float]]] = {}
_line_paths: dict[str, dict[tuple[str, str], list[tuple[float, float]]]] = {}
_line_meta: dict[str, dict[str, str]] = {}
_stops: dict[str, dict] = {}
_routes_queued: set[tuple[str, str]] = set()
_prefetch_current: list[str] = []
# (jid, line, direction, attempts) — direction to release the _routes_queued
# key when a route is given up on, attempts for the retry limit.
_route_queue: deque[tuple[str, str, str, int]] = deque()

# Most recent vehicle snapshot from the background poller's city-wide bbox,
# for /api/stats/live and the history sampler. Browser viewport polls never
# write it — a zoomed-in map would otherwise skew the stats and history.
_last_vehicle_snapshot: list[dict] = []
_last_vehicle_snapshot_at: float = 0.0  # wall-clock time of the KVB fetch
_last_sampled_snapshot_at: float = 0.0

# When a browser last asked for live data (monotonic); wakes an idle poller.
_last_viewer_at: float = float("-inf")
_viewer_wake = threading.Event()

# Opened lazily (main() or first use), so importing this module never creates
# or migrates a database file.
_history: HistoryStore | None = None
_history_init_lock = threading.Lock()


def _get_history() -> HistoryStore | None:
    global _history
    if _history is not None or not HISTORY_ENABLED:
        return _history
    with _history_init_lock:
        if _history is None:
            _history = HistoryStore(DB_FILE, late_from=LATE_FROM)
    return _history


def _log(prefix: str, message: str, exc: BaseException | None = None) -> None:
    detail = f" ({type(exc).__name__}: {exc})" if exc is not None else ""
    print(f"[{prefix}] {message}{detail}", file=sys.stderr)


def _backoff(failures: int, base: float, cap: float = ERROR_BACKOFF_CAP_SECONDS) -> float:
    return min(cap, base * 2 ** max(0, failures - 1))


def _pause(seconds: float) -> None:
    """Background-thread sleep; a seam for tests."""
    time.sleep(seconds)


# --------------------------------------------------------------------------
# Geometry persistence / prefetch — ported near-verbatim from map_server.py.
# --------------------------------------------------------------------------
def _load_geometry() -> None:
    try:
        raw = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    with _lock:
        _line_meta.update(raw.get("line_meta", {}))
        for line, paths in raw.get("line_paths", {}).items():
            segments = {tuple(key.split("|", 1)): [tuple(point) for point in path] for key, path in paths.items()}
            _line_paths.setdefault(line, {}).update(segments)
            _segments.update(segments)
        for ext_id, stop in raw.get("stops", {}).items():
            entry = _stops.setdefault(ext_id, {**stop, "lines": set(), "cats": set()})
            entry["lines"].update(stop.get("lines", ()))
            entry["cats"].update(stop.get("cats", ()))
    _load_rail_geometry()


def _rail_geometry_sources() -> list:
    """DATA_DIR first (local override / dev checkout), then the copy bundled
    into the wheel as package data."""
    sources: list = [RAIL_FILE]
    try:
        sources.append(resources.files("kvb_hafas.server").joinpath("data", "rail_geometry.json"))
    except (ModuleNotFoundError, TypeError, OSError):  # pragma: no cover - exotic loaders
        pass
    return sources


def _read_rail_geometry() -> dict | None:
    for source in _rail_geometry_sources():
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except OSError:
            continue
        except ValueError as exc:
            _log("geometry", f"ignoring unreadable {source}", exc)
            continue
        if isinstance(raw, dict):
            return raw
    _log("geometry", "rail_geometry.json not found in DATA_DIR or package; rail lines use HAFAS polylines only")
    return None


def _load_rail_geometry() -> None:
    raw = _read_rail_geometry()
    if not raw:
        return
    paths = {key: [tuple(point) for point in path] for key, path in raw.items()}

    def lookup(key: tuple[str, str]) -> list | None:
        return paths.get("|".join(station_ext_id(part) or part for part in key))

    with _lock:
        for segments in (_segments, *_line_paths.values()):
            for key in list(segments):
                path = lookup(key)
                if path:
                    segments[key] = path


def _save_geometry() -> None:
    with _lock:
        payload = {
            "line_meta": dict(_line_meta),
            "line_paths": {
                line: {f"{a}|{b}": path for (a, b), path in segments.items()} for line, segments in _line_paths.items()
            },
            "stops": {
                ext_id: {**stop, "lines": sorted(stop["lines"]), "cats": sorted(stop["cats"])}
                for ext_id, stop in _stops.items()
            },
        }
    tmp = GEOMETRY_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, GEOMETRY_FILE)
    except OSError:
        pass


def _requeue_routes(batch: list[tuple[str, str, str, int]]) -> None:
    """Failed batch: back to the end of the queue, unless a route has used up
    its attempts — then release its key so a later sighting can queue it anew."""
    with _lock:
        for jid, line, direction, attempts in batch:
            if attempts + 1 < PREFETCH_MAX_ATTEMPTS:
                _route_queue.append((jid, line, direction, attempts + 1))
            else:
                _routes_queued.discard((line, direction))


def _prefetch_batch() -> bool:
    """Fetch and merge one batch of queued routes. False if the queue was
    empty. A failed batch is requeued and the exception re-raised."""
    with _lock:
        batch = [_route_queue.popleft() for _ in range(min(PREFETCH_CHUNK, len(_route_queue)))]
        _prefetch_current[:] = sorted({line for _, line, _, _ in batch if line})
    if not batch:
        return False
    try:
        results = _client.journey_details_many([jid for jid, _, _, _ in batch], chunk=PREFETCH_CHUNK)
        with _lock:
            for (_, line, _, _), (segments, stops) in zip(batch, results):
                _merge_segments(_segments, segments)
                _merge_segments(_line_paths.setdefault(line, {}), segments)
                _merge_stops(stops, line)
        if any(segments for segments, _ in results):
            _save_geometry()
    except Exception:
        _requeue_routes(batch)
        raise
    finally:
        with _lock:
            _prefetch_current.clear()
    return True


def _prefetch_geometry() -> None:
    failures = 0
    while True:
        try:
            fetched = _prefetch_batch()
            failures = 0
        except Exception as exc:  # the thread must never die
            failures += 1
            delay = _backoff(failures, 5.0)
            _log("prefetch", f"batch failed, retrying in {delay:.0f}s", exc)
            _pause(delay)
            continue
        if not fetched:
            _pause(2.0)


def _merge_segments(target: dict, new: dict) -> None:
    for key, path in new.items():
        if len(path) >= len(target.get(key, ())):
            target[key] = path


def _merge_stops(stops, line: str) -> None:
    category = _line_meta.get(line, {}).get("category", "")
    for stop in stops:
        entry = _stops.setdefault(
            stop.ext_id, {"name": stop.name, "lat": stop.lat, "lon": stop.lon, "lines": set(), "cats": set()}
        )
        entry["lines"].add(line)
        if category:
            entry["cats"].add(category)


def _queue_routes(vehicles) -> None:
    with _lock:
        for vehicle in vehicles:
            _line_meta.setdefault(
                vehicle.line,
                {"colour": vehicle.colour, "text_colour": vehicle.text_colour, "category": vehicle.category},
            )
            key = (vehicle.line, vehicle.direction)
            if key not in _routes_queued:
                _routes_queued.add(key)
                _route_queue.append((vehicle.jid, vehicle.line, vehicle.direction, 0))


def _network() -> dict:
    with _lock:
        lines = []
        for name, segments in sorted(_line_paths.items()):
            if not name:
                continue
            meta = _line_meta.get(name, {})
            lines.append(
                {
                    "line": name,
                    "colour": meta.get("colour", ""),
                    "category": meta.get("category", ""),
                    "paths": [[[round(lat, 5), round(lon, 5)] for lat, lon in path] for path in segments.values()],
                }
            )
    return {"lines": lines}


def _stops_payload() -> dict:
    with _lock:
        stops = [
            {
                "id": ext_id,
                "name": stop["name"],
                "lat": round(stop["lat"], 5),
                "lon": round(stop["lon"], 5),
                "lines": sorted(stop["lines"]),
                "cats": sorted(stop["cats"]),
            }
            for ext_id, stop in _stops.items()
            if stop["lat"] is not None and stop["lon"] is not None
        ]
    return {"stops": stops}


def _vehicles(bbox: tuple[float, float, float, float], snapshot: bool = False) -> dict:
    """Vehicles in `bbox`, cached per rounded bbox. Only the background poller
    passes snapshot=True; its city-wide result feeds stats and history."""
    global _last_vehicle_snapshot, _last_vehicle_snapshot_at
    key = tuple(round(v, 2) for v in bbox)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < VEHICLE_POLL_CACHE_TTL_SECONDS:
            if snapshot:
                _last_vehicle_snapshot = cached[1]["vehicles"]
                _last_vehicle_snapshot_at = cached[1]["served_at"] / 1000
            return cached[1]
        segments = dict(_segments)
    vehicles = _client.vehicle_positions(*bbox, max_vehicles=MAX_VEHICLES, segments=segments)
    _queue_routes(vehicles)
    with _lock:
        pending, queued, current = len(_route_queue), len(_routes_queued), list(_prefetch_current)
    vehicle_dicts = [asdict(v) for v in vehicles]
    payload = {
        "served_at": time.time() * 1000,
        "track_seconds": TRACK_SECONDS,
        "known_segments": len(segments),
        "prefetch": {"pending": pending, "queued": queued, "current": current},
        "vehicles": vehicle_dicts,
    }
    now = time.monotonic()
    with _lock:
        _cache.pop(key, None)
        _prune_cache(_cache, now, VEHICLE_POLL_CACHE_TTL_SECONDS, VEHICLE_CACHE_MAX_ENTRIES)
        _cache[key] = (now, payload)
        if snapshot:
            _last_vehicle_snapshot = vehicle_dicts
            _last_vehicle_snapshot_at = payload["served_at"] / 1000
    return payload


def _note_viewer() -> None:
    """A browser asked for live data: keep the poller at full rate."""
    global _last_viewer_at
    now = time.monotonic()
    was_idle = now - _last_viewer_at >= VIEWER_IDLE_SECONDS
    _last_viewer_at = now
    if was_idle:
        _viewer_wake.set()


def _poll_interval(now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    if now - _last_viewer_at < VIEWER_IDLE_SECONDS:
        return VEHICLE_POLL_CACHE_TTL_SECONDS
    if HISTORY_ENABLED:
        return max(VEHICLE_POLL_CACHE_TTL_SECONDS, HISTORY_SAMPLE_INTERVAL_SECONDS)
    return max(VEHICLE_POLL_CACHE_TTL_SECONDS, IDLE_POLL_SECONDS)


def _wait_for_next_poll(seconds: float) -> None:
    """Sleep until the next poll, or earlier when a browser shows up again."""
    _viewer_wake.wait(seconds)
    _viewer_wake.clear()


def _vehicle_snapshot_poller() -> None:
    """Keeps _last_vehicle_snapshot fresh independent of whether a map.html
    tab is open — /api/stats/live and the history sampler both only ever
    read that global. Slows down while nobody is watching."""
    bbox = (VEHICLE_SNAPSHOT_MIN_LAT, VEHICLE_SNAPSHOT_MIN_LON, VEHICLE_SNAPSHOT_MAX_LAT, VEHICLE_SNAPSHOT_MAX_LON)
    failures = 0
    while True:
        try:
            _vehicles(bbox, snapshot=True)
            failures = 0
        except Exception as exc:  # the thread must never die
            failures += 1
            _log("poller", "vehicle snapshot failed", exc)
        interval = _poll_interval()
        if failures:
            interval = max(interval, _backoff(failures, VEHICLE_POLL_CACHE_TTL_SECONDS))
        _wait_for_next_poll(interval)


def _service_alerts() -> dict:
    global _alerts
    with _lock:
        if _alerts and time.monotonic() - _alerts[0] < ALERT_CACHE_TTL_SECONDS:
            return {"alerts": _alerts[1]}
    alerts = [
        {**asdict(alert), "stops": list(alert.stops)}
        for alert in _client.service_alerts(max_num=120)
        if alert.category != ALERT_AD_CATEGORY
    ]
    with _lock:
        _alerts = (time.monotonic(), alerts)
    return {"alerts": alerts}


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(p) for p in raw.split(",")]
    except ValueError:
        raise ValueError("bbox needs four numbers: minLat,minLon,maxLat,maxLon") from None
    if len(parts) != 4:
        raise ValueError("bbox needs four values: minLat,minLon,maxLat,maxLon")
    min_lat, min_lon, max_lat, max_lon = parts
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox implausible")
    if max_lat - min_lat > 1.0 or max_lon - min_lon > 1.0:
        raise ValueError("bbox too large (max. 1 degree per axis)")
    return min_lat, min_lon, max_lat, max_lon


# --------------------------------------------------------------------------
# Query-parameter parsing. Every helper raises ValueError with a short,
# generic message (never echoing the raw input) that the handler returns as 400.
# --------------------------------------------------------------------------
def _param(query: dict, name: str) -> str | None:
    values = query.get(name)
    if not values or values[0] == "":
        return None
    return values[0]


def _parse_count(query: dict, name: str, default: int, maximum: int) -> int:
    """Positive integer, clamped to 1..maximum."""
    raw = _param(query, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"'{name}' must be an integer") from None
    return max(1, min(maximum, value))


def _parse_ts(query: dict, name: str) -> int | None:
    """Unix timestamp in seconds within TS_MIN..TS_MAX, or None if absent."""
    raw = _param(query, name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (ValueError, OverflowError):
        raise ValueError(f"'{name}' must be a unix timestamp") from None
    if not TS_MIN <= value <= TS_MAX:
        raise ValueError(f"'{name}' out of range")
    return value


def _hour_floor(ts: int | None) -> int | None:
    # The summary tables are hour-granular, so these give identical results —
    # and keep the stats-cache key space small.
    return None if ts is None else ts // 3600 * 3600


# --------------------------------------------------------------------------
# Stop search / departures
# --------------------------------------------------------------------------
def _stops_search(query: str, max_results: int) -> dict:
    stops = _client.find_stops(query, max_results=max_results)
    return {"stops": [asdict(s) for s in stops]}


def _stop_details(stop_ext_id: str) -> dict:
    return asdict(_client.stop_details(stop_ext_id))


def _departures(stop_id: str, max_journeys: int) -> dict:
    deps = _client.station_board(stop_id, max_journeys=max_journeys)
    out = []
    for d in deps:
        payload = asdict(d)
        payload["delay_minutes"] = _delay_minutes(d.planned, d.realtime)
        out.append(payload)
    return {"departures": out}


# --------------------------------------------------------------------------
# Journey ETA — "wann ist diese Bahn bei mir?"
# --------------------------------------------------------------------------
def _journey(jid: str) -> tuple:
    now = time.monotonic()
    with _lock:
        hit = _journeys.get(jid)
        if hit and now - hit[0] < JOURNEY_CACHE_TTL_SECONDS:
            return hit[1]
        for key in [k for k, (at, _) in _journeys.items() if now - at >= JOURNEY_CACHE_TTL_SECONDS]:
            del _journeys[key]
    value = _client.journey_route_with_segments(jid)
    with _lock:
        _journeys[jid] = (now, value)
    return value


def _journey_eta(jid: str, lat: float, lon: float) -> dict:
    route, fetched = _journey(jid)
    with _lock:
        # Prefer the finer cached paths (OSM rail geometry, longest prefetch
        # result) over this response's own polyline, like the map does.
        segments = {**fetched, **{key: _segments[key] for key in fetched if key in _segments}}
    return journey_eta(route, segments, (lat, lon), time.time())


def _parse_point(query: dict) -> tuple[float, float]:
    try:
        lat, lon = float(query.get("lat", [""])[0]), float(query.get("lon", [""])[0])
    except ValueError:
        raise ValueError("lat/lon must be numbers") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon implausible")
    return lat, lon


# --------------------------------------------------------------------------
# History sampling / retention — background threads.
# --------------------------------------------------------------------------
def _sample_once(now: float | None = None) -> int:
    """Record the current poller snapshot once. Skips an empty, already
    recorded or stale snapshot. Returns the number of vehicles recorded."""
    global _last_sampled_snapshot_at
    history = _get_history()
    if history is None:
        return 0
    with _lock:
        snapshot, taken_at = list(_last_vehicle_snapshot), _last_vehicle_snapshot_at
    now = time.time() if now is None else now
    if not snapshot or taken_at == _last_sampled_snapshot_at:
        return 0
    if now - taken_at > SNAPSHOT_MAX_AGE_SECONDS:
        return 0  # poller has not refreshed it (KVB down?) — don't record old positions as new
    if HISTORY_FILTER_LOCAL_ONLY:
        snapshot = [v for v in snapshot if is_kvb_local(v.get("category"))]
    count = history.record_vehicles(snapshot)
    _last_sampled_snapshot_at = taken_at
    return count


def _history_sampler() -> None:
    if not HISTORY_ENABLED:
        return
    failures = 0
    while True:
        delay = HISTORY_SAMPLE_INTERVAL_SECONDS
        if failures:
            delay = max(delay, _backoff(failures, HISTORY_SAMPLE_INTERVAL_SECONDS))
        _pause(delay)
        try:
            _sample_once()
            failures = 0
        except Exception as exc:  # sampler must never crash the server
            failures += 1
            _log("history", "sample failed", exc)


def _purge_once() -> int:
    history = _get_history()
    if history is None:
        return 0
    removed = history.purge_older_than(HISTORY_RETENTION_DAYS)
    if removed:
        print(f"[history] purged {removed} rows older than {HISTORY_RETENTION_DAYS} days")
    return removed


def _history_purger() -> None:
    if not HISTORY_ENABLED:
        return
    delay: float = PURGE_STARTUP_DELAY_SECONDS
    failures = 0
    while True:
        _pause(delay)
        try:
            _purge_once()
            failures = 0
            delay = PURGE_INTERVAL_SECONDS
        except Exception as exc:
            failures += 1
            delay = _backoff(failures, 60.0, cap=PURGE_INTERVAL_SECONDS)
            _log("history", "purge failed", exc)


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
HTML = "text/html; charset=utf-8"
STATIC_FILES = {
    "/": ("index.html", HTML),
    "/index.html": ("index.html", HTML),
    "/map.html": ("map.html", HTML),
    "/departures.html": ("departures.html", HTML),
    "/dashboard.html": ("dashboard.html", HTML),
    "/shared.css": ("shared.css", "text/css; charset=utf-8"),
    "/shared.js": ("shared.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

# path -> Handler method taking the parsed query.
API_ROUTES = {
    "/api/config": "_config_route",
    "/api/vehicles": "_vehicles_route",
    "/api/alerts": "_alerts_route",
    "/api/network": "_network_route",
    "/api/stops": "_stops_route",
    "/api/stops/search": "_stops_search_route",
    "/api/stops/details": "_stop_details_route",
    "/api/departures": "_departures_route",
    "/api/journey/eta": "_journey_eta_route",
    "/api/stats/live": "_stats_live_route",
    "/api/stats/delays": "_stats_delays_route",
    "/api/stats/punctuality-trend": "_stats_punctuality_trend_route",
    "/api/stats/punctuality-by-hour": "_stats_punctuality_by_hour_route",
    "/api/history/trip": "_history_trip_route",
    "/api/history/vehicles": "_history_vehicles_route",
}

# Live-data endpoints a browser polls; any hit keeps the snapshot poller fast.
VIEWER_ROUTES = {"/api/vehicles", "/api/stats/live"}

# 200 bodies for history endpoints while HISTORY_ENABLED is off; the
# dashboard recognises "history disabled".
HISTORY_DISABLED_PAYLOADS = {
    "/api/stats/delays": lambda: {
        "error": "history disabled",
        "max_delay_minutes": None,
        "max_delay_detail": None,
        "lines": [],
        "least_punctual_lines": [],
    },
    "/api/stats/punctuality-trend": lambda: {"error": "history disabled", "buckets": [], "lines": {}},
    "/api/stats/punctuality-by-hour": lambda: {
        "error": "history disabled",
        "hours": list(range(24)),
        "lines": {},
        "counts": {},
    },
    "/api/history/trip": lambda: {"observations": []},
    "/api/history/vehicles": lambda: {"observations": []},
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # Inline scripts/styles are part of the UI; map tiles come from any
    # configurable (TILE_URL) host. No frame-ancestors: HA dashboards may
    # embed the add-on from another origin.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https: http:; connect-src 'self'; object-src 'none'; base-uri 'self'"
    ),
}


class Handler(BaseHTTPRequestHandler):
    # Drop idle/slow connections instead of tying up a thread forever.
    timeout = 30

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path

        static = STATIC_FILES.get(path)
        if static:
            self._file(*static)
            return
        if path.startswith("/vendor/"):
            self._vendor(path[len("/vendor/") :])
            return
        route = API_ROUTES.get(path)
        if route is None:
            self._json(404, {"error": "not found"})
            return
        if path in VIEWER_ROUTES:
            _note_viewer()
        getattr(self, route)(path, parse_qs(url.query))

    def _config_payload(self) -> dict:
        # Values the frontend needs at runtime but that live in add-on options
        # (or their standalone-dev defaults), so pages don't hardcode them.
        return {
            "ingress_path": self.headers.get("X-Ingress-Path", ""),
            "vehicle_poll_cache_ttl_seconds": VEHICLE_POLL_CACHE_TTL_SECONDS,
            "alert_cache_ttl_seconds": ALERT_CACHE_TTL_SECONDS,
            "default_map_center": {"lat": DEFAULT_MAP_CENTER_LAT, "lon": DEFAULT_MAP_CENTER_LON},
            "default_zoom": DEFAULT_ZOOM,
            "favorite_stop_ids": FAVORITE_STOP_IDS,
            "dashboard_refresh_seconds": DASHBOARD_REFRESH_SECONDS,
            "history_enabled": HISTORY_ENABLED,
            "track_seconds": TRACK_SECONDS,
            "late_from_minutes": LATE_FROM,
            "tile_url": TILE_URL,
            "tile_attribution": TILE_ATTRIBUTION,
        }

    def _file(self, name: str, ctype: str) -> None:
        try:
            body = WEBUI.joinpath(name).read_bytes()
        except OSError:
            self._json(404, {"error": "not found"})
            return
        self._send(200, ctype, body)

    def _vendor(self, rel: str) -> None:
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            self._json(404, {"error": "not found"})
            return
        target = WEBUI.joinpath("vendor", rel)
        try:
            body = target.read_bytes()
        except (OSError, IsADirectoryError):
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        self._send(200, ctype, body, cache="public, max-age=31536000, immutable")

    def _history_or_disabled(self, path: str) -> HistoryStore | None:
        """The history store, or None after answering with the endpoint's
        "history disabled" payload."""
        history = _get_history()
        if history is None:
            self._json(200, HISTORY_DISABLED_PAYLOADS[path]())
        return history

    def _bad_request(self, exc: ValueError) -> None:
        self._json(400, {"error": str(exc)})

    def _config_route(self, path: str, query: dict) -> None:
        self._json(200, self._config_payload())

    def _alerts_route(self, path: str, query: dict) -> None:
        self._guarded(_service_alerts)

    def _network_route(self, path: str, query: dict) -> None:
        self._guarded(_network)

    def _stops_route(self, path: str, query: dict) -> None:
        self._guarded(_stops_payload)

    def _vehicles_route(self, path: str, query: dict) -> None:
        try:
            bbox = _parse_bbox(query.get("bbox", [""])[0])
        except ValueError as exc:
            self._bad_request(exc)
            return
        self._guarded(lambda: _vehicles(bbox))

    def _stops_search_route(self, path: str, query: dict) -> None:
        q = (query.get("q", [""])[0]).strip()
        if not q:
            self._json(400, {"error": "missing query parameter 'q'"})
            return
        try:
            max_results = _parse_count(query, "max", 5, MAX_STOP_SEARCH_RESULTS)
        except ValueError as exc:
            self._bad_request(exc)
            return
        self._guarded(lambda: _stops_search(q, max_results))

    def _stop_details_route(self, path: str, query: dict) -> None:
        ext_id = (query.get("ext_id", [""])[0]).strip()
        if not ext_id:
            self._json(400, {"error": "missing query parameter 'ext_id'"})
            return
        self._guarded(lambda: _stop_details(ext_id))

    def _departures_route(self, path: str, query: dict) -> None:
        stop_id = (query.get("stop_id", [""])[0]).strip()
        if not stop_id:
            self._json(400, {"error": "missing query parameter 'stop_id'"})
            return
        try:
            max_journeys = _parse_count(query, "max", 10, MAX_DEPARTURES)
        except ValueError as exc:
            self._bad_request(exc)
            return
        self._guarded(lambda: _departures(stop_id, max_journeys))

    def _journey_eta_route(self, path: str, query: dict) -> None:
        jid = (query.get("jid", [""])[0]).strip()
        if not jid:
            self._json(400, {"error": "missing query parameter 'jid'"})
            return
        try:
            lat, lon = _parse_point(query)
        except ValueError as exc:
            self._bad_request(exc)
            return
        self._guarded(lambda: _journey_eta(jid, lat, lon))

    def _stats_live_route(self, path: str, query: dict) -> None:
        mode = (query.get("mode", [None])[0]) or None
        with _lock:
            snapshot = list(_last_vehicle_snapshot)
        self._json(200, live_stats(snapshot, late_from=LATE_FROM, mode=mode))

    def _stats_delays_route(self, path: str, query: dict) -> None:
        history = self._history_or_disabled(path)
        if history is None:
            return
        try:
            ts_from = _hour_floor(_parse_ts(query, "from"))
            ts_to = _hour_floor(_parse_ts(query, "to"))
        except ValueError as exc:
            self._bad_request(exc)
            return

        def _combined() -> dict:
            stats = history.delay_stats(ts_from=ts_from, ts_to=ts_to)
            stats["max_delay_detail"] = history.max_delay_detail(ts_from=ts_from, ts_to=ts_to)
            return stats

        self._guarded(lambda: _stats_cached(("delays", ts_from, ts_to), STATS_CACHE_TTL_SECONDS, _combined))

    def _stats_punctuality_trend_route(self, path: str, query: dict) -> None:
        history = self._history_or_disabled(path)
        if history is None:
            return
        window = (query.get("window", ["7d"])[0]) or "7d"
        window_seconds, bucket_seconds = PUNCTUALITY_WINDOWS.get(window, PUNCTUALITY_WINDOWS["7d"])
        self._guarded(
            lambda: _stats_cached(
                ("trend", window_seconds, bucket_seconds),
                STATS_CACHE_TTL_SECONDS,
                lambda: history.punctuality_trend(window_seconds, bucket_seconds),
            )
        )

    def _stats_punctuality_by_hour_route(self, path: str, query: dict) -> None:
        history = self._history_or_disabled(path)
        if history is None:
            return
        window = (query.get("window", ["28d"])[0]) or "28d"
        window_seconds = PUNCTUALITY_HOUR_WINDOWS.get(window, PUNCTUALITY_HOUR_WINDOWS["28d"])
        days = (query.get("days", ["all"])[0]) or "all"
        if days not in DAY_TYPES:
            self._json(400, {"error": "'days' must be one of: " + ", ".join(DAY_TYPES)})
            return
        self._guarded(
            lambda: _stats_cached(
                ("hour", window_seconds, days),
                HEATMAP_CACHE_TTL_SECONDS,
                lambda: history.punctuality_by_hour(window_seconds, days=days),
            )
        )

    def _history_trip_route(self, path: str, query: dict) -> None:
        jid = (query.get("jid", [""])[0]).strip()
        if not jid:
            self._json(400, {"error": "missing query parameter 'jid'"})
            return
        history = self._history_or_disabled(path)
        if history is None:
            return
        self._guarded(lambda: {"observations": history.trip(jid)})

    def _history_vehicles_route(self, path: str, query: dict) -> None:
        history = self._history_or_disabled(path)
        if history is None:
            return
        line = (query.get("line", [None])[0]) or None
        try:
            ts_from = _parse_ts(query, "from")
            ts_to = _parse_ts(query, "to")
        except ValueError as exc:
            self._bad_request(exc)
            return
        # Default: the most recent hour (not the oldest rows in the DB).
        if ts_to is None:
            ts_to = int(time.time()) if ts_from is None else ts_from + HISTORY_VEHICLES_DEFAULT_WINDOW_SECONDS
        if ts_from is None:
            ts_from = ts_to - HISTORY_VEHICLES_DEFAULT_WINDOW_SECONDS
        if ts_to < ts_from:
            self._json(400, {"error": "'to' must not be before 'from'"})
            return
        if ts_to - ts_from > HISTORY_VEHICLES_MAX_WINDOW_SECONDS:
            self._json(400, {"error": f"time window too large (max. {HISTORY_VEHICLES_MAX_WINDOW_SECONDS} s)"})
            return
        self._guarded(lambda: {"observations": history.vehicles(line=line, ts_from=ts_from, ts_to=ts_to)})

    def _guarded(self, produce) -> None:
        # Error bodies stay generic: exception text can carry hosts, URLs or
        # internals. Details go to the log.
        try:
            body = produce()
        except (BrokenPipeError, ConnectionResetError):
            return
        except KVBHafasError as exc:
            _log("http", f"{self.path}: HAFAS error", exc)
            self._json(502, {"error": "HAFAS request failed"})
            return
        except OSError as exc:
            _log("http", f"{self.path}: KVB unreachable", exc)
            self._json(504, {"error": "KVB unreachable"})
            return
        except Exception as exc:  # last-resort guard so a bug never surfaces a raw traceback
            _log("http", f"{self.path}: internal error", exc)
            self._json(500, {"error": "internal error"})
            return
        try:
            self._json(200, body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _json(self, status: int, body: dict) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(body).encode())

    def _send(self, status: int, content_type: str, body: bytes, cache: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    _load_geometry()
    # Open (and if needed migrate/backfill) the history DB before serving,
    # not at import time.
    _get_history()
    threading.Thread(target=_prefetch_geometry, daemon=True).start()
    threading.Thread(target=_vehicle_snapshot_poller, daemon=True).start()
    if HISTORY_ENABLED:
        threading.Thread(target=_history_sampler, daemon=True).start()
        threading.Thread(target=_history_purger, daemon=True).start()
    print(f"KVB Live Map: http://{HOST}:{PORT}  (history {f'on, {DB_FILE}' if HISTORY_ENABLED else 'off'})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

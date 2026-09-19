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
from kvb_hafas.parsing import station_ext_id
from kvb_hafas.server.history_store import HistoryStore
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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


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
HISTORY_SAMPLE_INTERVAL_SECONDS = _env_int("HISTORY_SAMPLE_INTERVAL_SECONDS", 60)
HISTORY_RETENTION_DAYS = _env_int("HISTORY_RETENTION_DAYS", 365)
HISTORY_FILTER_LOCAL_ONLY = _env_bool("HISTORY_FILTER_LOCAL_ONLY", True)
VEHICLE_POLL_CACHE_TTL_SECONDS = _env_float("VEHICLE_POLL_CACHE_TTL_SECONDS", 15.0)
ALERT_CACHE_TTL_SECONDS = _env_float("ALERT_CACHE_TTL_SECONDS", 300.0)
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
DASHBOARD_REFRESH_SECONDS = _env_int("DASHBOARD_REFRESH_SECONDS", 15)
TILE_URL = os.environ.get("TILE_URL") or ""
TILE_ATTRIBUTION = os.environ.get("TILE_ATTRIBUTION") or ""

WEBUI = resources.files("kvb_hafas.webui")
# Relative to CWD by default — `uv run map_server.py` from the repo root finds
# `data/rail_geometry.json` there; kvb-ha-map's run.sh sets DATA_DIR=/app/data
# explicitly, so this only matters for local dev.
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
GEOMETRY_FILE = DATA_DIR / "map_geometry.json"
RAIL_FILE = DATA_DIR / "rail_geometry.json"
DB_FILE = DATA_DIR / "history.db"

TRACK_SECONDS = 120
PREFETCH_CHUNK = 10
MAX_VEHICLES = 600
ALERT_AD_CATEGORY = 99
LATE_FROM = 3
PURGE_INTERVAL_SECONDS = 3600 * 6  # purge check four times a day is plenty for a daily-granularity retention

# Dashboard punctuality-trend chart presets: window_seconds, bucket_seconds.
# Server-picked (not client-supplied bucket sizes) to keep the query cheap
# and bounded.
PUNCTUALITY_WINDOWS = {
    "7d": (7 * 86400, 86400),
    "24h": (24 * 3600, 3600),
    "1h": (3600, 300),
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

_segments: dict[tuple[str, str], list[tuple[float, float]]] = {}
_line_paths: dict[str, dict[tuple[str, str], list[tuple[float, float]]]] = {}
_line_meta: dict[str, dict[str, str]] = {}
_stops: dict[str, dict] = {}
_routes_queued: set[tuple[str, str]] = set()
_prefetch_current: list[str] = []
_route_queue: deque[tuple[str, str]] = deque()

# Most recent vehicle snapshot from ANY bbox poll, for the history sampler —
# reusing already-fetched data means it never triggers its own KVB request.
_last_vehicle_snapshot: list[dict] = []
_last_vehicle_snapshot_at: float = 0.0

_history: HistoryStore | None = HistoryStore(DB_FILE) if HISTORY_ENABLED else None


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


def _load_rail_geometry() -> None:
    try:
        raw = json.loads(RAIL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def _prefetch_geometry() -> None:
    while True:
        with _lock:
            batch = [_route_queue.popleft() for _ in range(min(PREFETCH_CHUNK, len(_route_queue)))]
            _prefetch_current[:] = sorted({line for _, line in batch if line})
        if not batch:
            time.sleep(2.0)
            continue
        try:
            results = _client.journey_details_many([jid for jid, _ in batch], chunk=PREFETCH_CHUNK)
        except (KVBHafasError, OSError):
            continue
        with _lock:
            for (_, line), (segments, stops) in zip(batch, results):
                _merge_segments(_segments, segments)
                _merge_segments(_line_paths.setdefault(line, {}), segments)
                _merge_stops(stops, line)
        if any(segments for segments, _ in results):
            _save_geometry()
        with _lock:
            _prefetch_current.clear()


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
                _route_queue.append((vehicle.jid, vehicle.line))


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


def _vehicles(bbox: tuple[float, float, float, float]) -> dict:
    global _last_vehicle_snapshot, _last_vehicle_snapshot_at
    key = tuple(round(v, 2) for v in bbox)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < VEHICLE_POLL_CACHE_TTL_SECONDS:
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
    with _lock:
        _cache[key] = (time.monotonic(), payload)
        _last_vehicle_snapshot = vehicle_dicts
        _last_vehicle_snapshot_at = time.time()
    return payload


def _vehicle_snapshot_poller() -> None:
    """Keeps _last_vehicle_snapshot fresh independent of whether a map.html
    tab is open — /api/stats/live and the history sampler both only ever
    read that global, which used to be written solely as a side effect of a
    browser's own /api/vehicles viewport poll."""
    bbox = (VEHICLE_SNAPSHOT_MIN_LAT, VEHICLE_SNAPSHOT_MIN_LON, VEHICLE_SNAPSHOT_MAX_LAT, VEHICLE_SNAPSHOT_MAX_LON)
    while True:
        try:
            _vehicles(bbox)
        except (KVBHafasError, OSError):
            pass  # transient KVB hiccup; next tick tries again
        time.sleep(VEHICLE_POLL_CACHE_TTL_SECONDS)


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
    parts = [float(p) for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox needs four values: minLat,minLon,maxLat,maxLon")
    min_lat, min_lon, max_lat, max_lon = parts
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox implausible")
    if max_lat - min_lat > 1.0 or max_lon - min_lon > 1.0:
        raise ValueError("bbox too large (max. 1 degree per axis)")
    return min_lat, min_lon, max_lat, max_lon


# --------------------------------------------------------------------------
# New: stop search / departures, delay computation (inlined — _delay_minutes
# is a private helper in kvb_hafas.parsing, not part of the public API, so we
# re-implement the same simple HHMMSS(-with-day-rollover-prefix) diff here
# rather than depend on an unexported name across library versions).
# --------------------------------------------------------------------------
def _delay_minutes(planned: str | None, realtime: str | None) -> int | None:
    if not planned or not realtime:
        return None

    def minutes(t: str) -> int:
        days = int(t[:-6] or 0) if len(t) > 6 else 0
        t = t[-6:]
        return days * 1440 + int(t[:2]) * 60 + int(t[2:4])

    try:
        return minutes(realtime) - minutes(planned)
    except ValueError:
        return None


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
# History sampling / retention — background threads.
# --------------------------------------------------------------------------
def _history_sampler() -> None:
    if not _history:
        return
    while True:
        time.sleep(HISTORY_SAMPLE_INTERVAL_SECONDS)
        with _lock:
            snapshot = list(_last_vehicle_snapshot)
        if not snapshot:
            continue  # nobody has polled /api/vehicles since the last tick — nothing to sample
        if HISTORY_FILTER_LOCAL_ONLY:
            snapshot = [v for v in snapshot if is_kvb_local(v.get("category"))]
        try:
            _history.record_vehicles(snapshot)
        except Exception as exc:  # pragma: no cover - sampler must never crash the server
            print(f"[history] sample failed: {exc}", file=sys.stderr)


def _history_purger() -> None:
    if not _history:
        return
    while True:
        time.sleep(PURGE_INTERVAL_SECONDS)
        try:
            removed = _history.purge_older_than(HISTORY_RETENTION_DAYS)
            if removed:
                print(f"[history] purged {removed} rows older than {HISTORY_RETENTION_DAYS} days")
        except Exception as exc:  # pragma: no cover
            print(f"[history] purge failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
STATIC_FILES = {"/map.html", "/departures.html", "/dashboard.html", "/index.html"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path

        if path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif path in STATIC_FILES:
            self._file(path.lstrip("/"), "text/html; charset=utf-8")
        elif path.startswith("/vendor/"):
            self._vendor(path[len("/vendor/") :])
        elif path == "/shared.css":
            self._file("shared.css", "text/css; charset=utf-8")
        elif path == "/favicon.svg":
            self._file("favicon.svg", "image/svg+xml")
        elif path == "/api/config":
            self._json(200, self._config_payload())
        elif path == "/api/vehicles":
            self._vehicles_route(parse_qs(url.query).get("bbox", [""])[0])
        elif path == "/api/alerts":
            self._guarded(_service_alerts)
        elif path == "/api/network":
            self._guarded(_network)
        elif path == "/api/stops":
            self._guarded(_stops_payload)
        elif path == "/api/stops/search":
            self._stops_search_route(parse_qs(url.query))
        elif path == "/api/stops/details":
            self._stop_details_route(parse_qs(url.query))
        elif path == "/api/departures":
            self._departures_route(parse_qs(url.query))
        elif path == "/api/stats/live":
            self._stats_live_route(parse_qs(url.query))
        elif path == "/api/stats/delays":
            self._stats_delays_route(parse_qs(url.query))
        elif path == "/api/stats/punctuality-trend":
            self._stats_punctuality_trend_route(parse_qs(url.query))
        elif path == "/api/history/trip":
            self._history_trip_route(parse_qs(url.query))
        elif path == "/api/history/vehicles":
            self._history_vehicles_route(parse_qs(url.query))
        else:
            self._json(404, {"error": "not found"})

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

    def _vehicles_route(self, raw_bbox: str) -> None:
        try:
            bbox = _parse_bbox(raw_bbox)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._guarded(lambda: _vehicles(bbox))

    def _stops_search_route(self, query: dict) -> None:
        q = (query.get("q", [""])[0]).strip()
        if not q:
            self._json(400, {"error": "missing query parameter 'q'"})
            return
        try:
            max_results = int(query.get("max", ["5"])[0])
        except ValueError:
            max_results = 5
        self._guarded(lambda: _stops_search(q, max_results))

    def _stop_details_route(self, query: dict) -> None:
        ext_id = (query.get("ext_id", [""])[0]).strip()
        if not ext_id:
            self._json(400, {"error": "missing query parameter 'ext_id'"})
            return
        self._guarded(lambda: _stop_details(ext_id))

    def _departures_route(self, query: dict) -> None:
        stop_id = (query.get("stop_id", [""])[0]).strip()
        if not stop_id:
            self._json(400, {"error": "missing query parameter 'stop_id'"})
            return
        try:
            max_journeys = int(query.get("max", ["10"])[0])
        except ValueError:
            max_journeys = 10
        self._guarded(lambda: _departures(stop_id, max_journeys))

    def _stats_live_route(self, query: dict) -> None:
        mode = (query.get("mode", [None])[0]) or None
        with _lock:
            snapshot = list(_last_vehicle_snapshot)
        self._json(200, live_stats(snapshot, late_from=LATE_FROM, mode=mode))

    def _stats_delays_route(self, query: dict) -> None:
        if not _history:
            self._json(
                200,
                {
                    "error": "history disabled",
                    "max_delay_minutes": None,
                    "max_delay_detail": None,
                    "lines": [],
                    "least_punctual_lines": [],
                },
            )
            return
        ts_from = self._int_or_none(query.get("from", [None])[0])
        ts_to = self._int_or_none(query.get("to", [None])[0])

        def _combined() -> dict:
            stats = _history.delay_stats(ts_from=ts_from, ts_to=ts_to)
            stats["max_delay_detail"] = _history.max_delay_detail(ts_from=ts_from, ts_to=ts_to)
            return stats

        self._guarded(_combined)

    def _stats_punctuality_trend_route(self, query: dict) -> None:
        if not _history:
            self._json(200, {"error": "history disabled", "buckets": [], "lines": {}})
            return
        window = (query.get("window", ["7d"])[0]) or "7d"
        window_seconds, bucket_seconds = PUNCTUALITY_WINDOWS.get(window, PUNCTUALITY_WINDOWS["7d"])
        self._guarded(lambda: _history.punctuality_trend(window_seconds, bucket_seconds))

    def _history_trip_route(self, query: dict) -> None:
        jid = (query.get("jid", [""])[0]).strip()
        if not jid:
            self._json(400, {"error": "missing query parameter 'jid'"})
            return
        if not _history:
            self._json(200, {"observations": []})
            return
        self._guarded(lambda: {"observations": _history.trip(jid)})

    def _history_vehicles_route(self, query: dict) -> None:
        if not _history:
            self._json(200, {"observations": []})
            return
        line = (query.get("line", [None])[0]) or None
        ts_from = self._int_or_none(query.get("from", [None])[0])
        ts_to = self._int_or_none(query.get("to", [None])[0])
        self._guarded(lambda: {"observations": _history.vehicles(line=line, ts_from=ts_from, ts_to=ts_to)})

    @staticmethod
    def _int_or_none(raw: str | None) -> int | None:
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _guarded(self, produce) -> None:
        try:
            self._json(200, produce())
        except (BrokenPipeError, ConnectionResetError):
            return
        except KVBHafasError as exc:
            self._json(502, {"error": f"HAFAS: {exc}"})
        except OSError as exc:
            self._json(504, {"error": f"KVB unreachable: {exc}"})
        except Exception as exc:  # pragma: no cover - last-resort guard so a bug never surfaces a raw traceback
            self._json(500, {"error": f"internal error: {exc}"})

    def _json(self, status: int, body: dict) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(body).encode())

    def _send(self, status: int, content_type: str, body: bytes, cache: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    _load_geometry()
    threading.Thread(target=_prefetch_geometry, daemon=True).start()
    threading.Thread(target=_vehicle_snapshot_poller, daemon=True).start()
    if HISTORY_ENABLED:
        threading.Thread(target=_history_sampler, daemon=True).start()
        threading.Thread(target=_history_purger, daemon=True).start()
    print(f"KVB Live Map: http://{HOST}:{PORT}  (history {'on' if HISTORY_ENABLED else 'off'})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

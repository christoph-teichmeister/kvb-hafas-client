"""Live-Kartenansicht der KVB-Fahrzeuge: `uv run map_server.py`, dann http://localhost:8000.

Winziger stdlib-Server, damit der Browser an `vehicle_positions()` kommt — der
HAFAS-Endpunkt selbst ist per CORS nicht direkt aus einer Seite aufrufbar.

Die Positionen sind hochgerechnet (`trainPosMode: CALC`), kein GPS; die
Bounding-Box liefert auch Fahrzeuge anderer Betreiber im Bediengebiet.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from collections import deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kvb_hafas import KVBHafasClient, KVBHafasError
from kvb_hafas.parsing import station_ext_id

HOST, PORT = "127.0.0.1", 8000
MAP_HTML = Path(__file__).with_name("map.html")
VENDOR_DIR = Path(__file__).with_name("vendor").resolve()  # Leaflet, lokal statt vom CDN
DATA_DIR = Path(__file__).parent / "data"
GEOMETRY_FILE = DATA_DIR / "map_geometry.json"
# Aus OpenStreetMap geroutete Gleisverläufe (tools/fetch_rail_geometry.py). Eigene
# Datei, damit ein Löschen des HAFAS-Caches die teure Arbeit nicht mitnimmt.
RAIL_FILE = DATA_DIR / "rail_geometry.json"
CACHE_TTL = 15.0  # s — mehrere Tabs/Reloads sollen die KVB-Infrastruktur nicht doppelt treffen
ALERT_TTL = 300.0  # s — Störungsmeldungen ändern sich im Minutentakt, nicht im Sekundentakt
TRACK_SECONDS = 120  # Länge des ani-Tracks, den HAFAS mitliefert
PREFETCH_CHUNK = 10  # Fahrten pro gebündeltem JourneyDetails-Request
# Default 100 deckelt in Köln sichtbar; ~340 Fahrzeuge sind im Innenstadt-
# Ausschnitt tatsächlich unterwegs, darüber liefert der Server nicht mehr.
MAX_VEHICLES = 600
ALERT_AD_CATEGORY = 99  # KVB-Werbung, zwischen die echten Meldungen gemischt

# ponytail: ein globaler Lock plus dict-Cache. Reicht für einen lokalen
# Prototyp mit einer Handvoll Tabs; bei echter Last pro Bbox sperren.
_client = KVBHafasClient(min_interval=1.0)
_lock = threading.Lock()
_cache: dict[tuple[float, ...], tuple[float, dict]] = {}
_alerts: tuple[float, list[dict]] | None = None

# Streckenverläufe je Haltestellenpaar. Geometrie ist statisch, also kein TTL —
# der Cache füllt sich im Hintergrund und gilt für die Laufzeit des Servers.
_segments: dict[tuple[str, str], list[tuple[float, float]]] = {}
# Dieselben Abschnitte, aber nach Linie getrennt — Grundlage für /api/network.
_line_paths: dict[str, dict[tuple[str, str], list[tuple[float, float]]]] = {}
_line_meta: dict[str, dict[str, str]] = {}
# Halte je Mast-extId, Abfallprodukt desselben Prefetch: die JourneyDetails-
# Antwort trägt in common.locL Name und Koordinate jedes Halts.
_stops: dict[str, dict] = {}
_routes_queued: set[tuple[str, str]] = set()
_prefetch_current: list[str] = []  # Linien im gerade laufenden Block, für die Anzeige
_route_queue: deque[tuple[str, str]] = deque()


def _load_geometry() -> None:
    """Gespeicherte Streckenverläufe einlesen, damit ein Neustart nicht bei null anfängt.

    ponytail: kein TTL, keine Invalidierung — Streckenverläufe ändern sich zum
    Fahrplanwechsel, nicht täglich. Wer sie neu will, löscht die Datei.
    """
    try:
        raw = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}  # keine oder kaputte Datei — dann eben wieder von vorn
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
    """OSM-Gleisverläufe über die groben HAFAS-Abschnitte legen.

    Für DB-Produkte liefert HAFAS nur die Halte selbst als Polyline, also
    Luftlinien. Was tools/fetch_rail_geometry.py aufgelöst hat, ersetzt hier den
    geraden Abschnitt — überall, wo derselbe Halt-Paar-Schlüssel vorkommt.
    """
    try:
        raw = json.loads(RAIL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # ohne die Datei bleiben die Luftlinien stehen
    # Die Datei liegt auf Haltestellenebene, die Abschnitte hier auf Mastebene:
    # dieselbe Relation heißt je Fahrt und Bahnsteig anders (siehe station_ext_id).
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
    """Streckenverläufe atomar wegschreiben (halbe Datei nach Strg-C wäre schlimmer als keine)."""
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
        pass  # Cache ist Beschleunigung, kein Zustand — ein Schreibfehler darf nichts kosten


def _prefetch_geometry() -> None:
    """Holt Streckenverläufe für neu gesehene Linie/Richtung-Paare, blockweise.

    Ein JourneyDetails-Request pro Linie und Richtung (nicht pro Fahrzeug) —
    in Köln etwa 150 Stück. Die gehen zu PREFETCH_CHUNK gebündelt in einem POST
    raus, sonst kostet jede einzeln eine Rate-Limit-Pause. Bis eine Relation da
    ist, fahren die betroffenen Fahrzeuge auf der Luftlinie.
    """
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
            continue  # ein Block weniger im Cache, kein Grund den Worker zu beenden
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
    """Abschnitte übernehmen, aber nie einen feineren Verlauf durch einen gröberen ersetzen.

    Der Prefetch holt für dieselbe Relation immer wieder die HAFAS-Polyline; für
    Züge sind das zwei Punkte. Ohne diese Bremse würde jeder Durchlauf die aus
    OSM gerouteten Gleise wieder plattmachen.
    """
    for key, path in new.items():
        if len(path) >= len(target.get(key, ())):
            target[key] = path


def _merge_stops(stops, line: str) -> None:
    """Halte der Fahrt vormerken und ihnen Linie und Produktart anheften.

    Die Produktart kommt aus `_line_meta` der Linie, zu der die Fahrt gehört —
    damit unterscheidet die Karte Bus-, Stadtbahn- und Bahnhalte, ohne dass
    dafür ein einziger zusätzlicher Request nötig wäre. Ein Halt, den Bus und
    Stadtbahn bedienen, sammelt beide Kategorien.
    """
    category = _line_meta.get(line, {}).get("category", "")
    for stop in stops:
        entry = _stops.setdefault(
            stop.ext_id, {"name": stop.name, "lat": stop.lat, "lon": stop.lon, "lines": set(), "cats": set()}
        )
        entry["lines"].add(line)
        if category:
            entry["cats"].add(category)


def _queue_routes(vehicles) -> None:
    """Jede noch unbekannte Linie/Richtung einmal zum Nachladen vormerken."""
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
    """Bisher bekannte Streckenverläufe, nach Linie gruppiert und eingefärbt.

    Wächst mit dem Prefetch mit: was noch nicht geholt wurde, fehlt hier noch.
    Koordinaten auf fünf Nachkommastellen (gut 1 m) gerundet — das halbiert die
    Antwort, ohne dass man es auf der Karte sieht.
    """
    with _lock:
        lines = []
        for name, segments in sorted(_line_paths.items()):
            if not name:
                continue  # Fahrten ohne Linienlabel (Werkstattfahrten o.ä.)
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
    """Bekannte Halte mit Name, Koordinate, Linien und Produktarten.

    Wie /api/network ein Abfallprodukt des Prefetch: was noch nicht geholt
    wurde, fehlt hier noch. Die Kategorien bleiben roh (HAFAS-`catOut`), die
    Karte fasst sie selbst zu Bus/Stadtbahn/Bahn zusammen.
    """
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
    """Fahrzeuge in der Box, gecacht. `served_at` verankert den Track im Browser."""
    key = tuple(round(v, 2) for v in bbox)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]
        segments = dict(_segments)
    vehicles = _client.vehicle_positions(*bbox, max_vehicles=MAX_VEHICLES, segments=segments)
    _queue_routes(vehicles)
    with _lock:
        # Wie viele Linie/Richtung-Paare noch auf ihren Streckenverlauf warten —
        # daraus macht die Karte ihren Fortschrittsbalken.
        pending, queued, current = len(_route_queue), len(_routes_queued), list(_prefetch_current)
    payload = {
        "served_at": time.time() * 1000,
        "track_seconds": TRACK_SECONDS,
        "known_segments": len(segments),
        "prefetch": {"pending": pending, "queued": queued, "current": current},
        "vehicles": [asdict(v) for v in vehicles],
    }
    with _lock:
        _cache[key] = (time.monotonic(), payload)
    return payload


def _service_alerts() -> dict:
    """Aktive Störungsmeldungen mit Koordinaten, ohne die eingestreute Werbung."""
    global _alerts
    with _lock:
        if _alerts and time.monotonic() - _alerts[0] < ALERT_TTL:
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
        raise ValueError("bbox braucht vier Werte: minLat,minLon,maxLat,maxLon")
    min_lat, min_lon, max_lat, max_lon = parts
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox unplausibel")
    # Riesige Ausschnitte würden den Server sinnlos belasten und liefern
    # ohnehin nur maxJny Fahrzeuge zurück.
    if max_lat - min_lat > 1.0 or max_lon - min_lon > 1.0:
        raise ValueError("bbox zu groß (max. 1 Grad je Achse)")
    return min_lat, min_lon, max_lat, max_lon


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — von BaseHTTPRequestHandler vorgegeben
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, "text/html; charset=utf-8", MAP_HTML.read_bytes())
        elif url.path.startswith("/vendor/"):
            self._vendor(url.path[len("/vendor/") :])
        elif url.path == "/api/vehicles":
            self._vehicles_route(parse_qs(url.query).get("bbox", [""])[0])
        elif url.path == "/api/alerts":
            self._guarded(_service_alerts)
        elif url.path == "/api/network":
            self._guarded(_network)
        elif url.path == "/api/stops":
            self._guarded(_stops_payload)
        else:
            self._json(404, {"error": "not found"})

    def _vendor(self, rel: str) -> None:
        """Mitgelieferte Leaflet-Dateien ausliefern, unbegrenzt cachebar (Version im Ordner fix)."""
        path = (VENDOR_DIR / rel).resolve()
        # Ohne diese Prüfung liefert `/vendor/../map_server.py` den Serverquelltext aus.
        if not path.is_relative_to(VENDOR_DIR) or not path.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, ctype, path.read_bytes(), cache="public, max-age=31536000, immutable")

    def _vehicles_route(self, raw_bbox: str) -> None:
        try:
            bbox = _parse_bbox(raw_bbox)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._guarded(lambda: _vehicles(bbox))

    def _guarded(self, produce) -> None:
        try:
            self._json(200, produce())
        except (BrokenPipeError, ConnectionResetError):
            return  # Client (Browser) hat abgebrochen — keine Antwort mehr möglich
        except KVBHafasError as exc:
            self._json(502, {"error": f"HAFAS: {exc}"})
        except OSError as exc:
            self._json(504, {"error": f"KVB nicht erreichbar: {exc}"})

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
        pass  # Zugriffslog rauschen lassen wir weg


if __name__ == "__main__":
    _load_geometry()
    threading.Thread(target=_prefetch_geometry, daemon=True).start()
    print(f"KVB-Livekarte: http://{HOST}:{PORT}  (Strg-C beendet)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

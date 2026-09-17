"""Echte Gleisverläufe für Zug/S-Bahn aus OpenStreetMap: `uv run tools/fetch_rail_geometry.py`.

HAFAS liefert für DB-Produkte nur die Halte selbst als Polyline (S19: 16 Punkte
auf 16 Halte), also Luftlinien quer über die Karte. Straßenbahn und Bus bekommen
dagegen echte Stützpunkte. Dieses Skript füllt die Lücke: Schienennetz einmal per
Overpass holen, jedes Haltestellenpaar darauf routen und das Ergebnis nach
rail_geometry.json schreiben. map_server.py legt die Pfade beim Start über die
groben HAFAS-Abschnitte.

Einmalig von Hand laufen lassen — Gleise ändern sich seltener als Fahrpläne.
"""

from __future__ import annotations

import heapq
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEOMETRY_FILE = ROOT / "map_geometry.json"
RAIL_FILE = ROOT / "rail_geometry.json"
OSM_CACHE = ROOT / ".cache" / "osm_rail.json"

# Nur der Kartenausschnitt plus Rand. Die gecachten Abschnitte reichen von Paris
# bis Berlin (ICE, THA) — dafür ein Schienennetz zu routen wäre absurd, und
# sichtbar ist davon ohnehin nichts.
REGION = (50.60, 6.40, 51.30, 7.55)  # min_lat, min_lon, max_lat, max_lon
RAIL_CATEGORIES = {"S-Bahn", "RE", "RB", "Regio", "ICE", "IC", "Fern", "THA", "SEV"}
SNAP_LIMIT_M = 250.0  # weiter weg heißt: der Halt liegt nicht an diesem Netz
DETOUR_LIMIT = 2.5  # Route länger als das Vielfache der Luftlinie -> falsch gesnappt
CELL = 0.01  # ~1 km Rasterzelle für die Nachbarsuche

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
)
# service!~"." wirft Abstell- und Rangiergleise raus, sonst schnappt die Suche
# im Bahnhofsvorfeld auf das erstbeste Nebengleis.
QUERY = (
    '[out:json][timeout:180];'
    'way["railway"~"^(rail|light_rail)$"]["service"!~"."]'
    "({0},{1},{2},{3});"
    "out geom;"
)


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Entfernung zweier (lat, lon)-Punkte in Metern."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def fetch_osm() -> dict:
    """Schienennetz der Region holen, Antwort auf Platte cachen."""
    if OSM_CACHE.exists():
        return json.loads(OSM_CACHE.read_text(encoding="utf-8"))
    data = urllib.parse.urlencode({"data": QUERY.format(*REGION)}).encode()
    for url in OVERPASS_MIRRORS:
        print(f"Overpass: {url} …", flush=True)
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "kvb-hafas-client/0.1"})
            with urllib.request.urlopen(req, timeout=300) as res:
                raw = res.read()
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"  fehlgeschlagen: {err}", flush=True)
            continue
        OSM_CACHE.parent.mkdir(exist_ok=True)
        OSM_CACHE.write_bytes(raw)
        return json.loads(raw)
    sys.exit("Kein Overpass-Spiegel erreichbar — später nochmal.")


def build_graph(osm: dict) -> dict[tuple[float, float], list[tuple[float, float]]]:
    """Wege zu einem Knotengraph verknüpfen; gemeinsame Koordinaten sind gemeinsame Knoten."""
    graph: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for way in osm.get("elements", []):
        points = [(round(p["lat"], 7), round(p["lon"], 7)) for p in way.get("geometry", [])]
        for u, v in zip(points, points[1:]):
            if u == v:
                continue
            graph.setdefault(u, []).append(v)
            graph.setdefault(v, []).append(u)
    return graph


def build_index(graph) -> dict[tuple[int, int], list[tuple[float, float]]]:
    """Grobes Raster über alle Knoten — lineare Nachbarsuche über 100k Knoten wäre zu langsam."""
    index: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for node in graph:
        index.setdefault((int(node[0] / CELL), int(node[1] / CELL)), []).append(node)
    return index


def snap(point: tuple[float, float], index) -> tuple[float, float] | None:
    """Nächster Netzknoten innerhalb SNAP_LIMIT_M, sonst None."""
    cy, cx = int(point[0] / CELL), int(point[1] / CELL)
    candidates = [n for dy in (-1, 0, 1) for dx in (-1, 0, 1) for n in index.get((cy + dy, cx + dx), ())]
    if not candidates:
        return None
    best = min(candidates, key=lambda n: haversine(point, n))
    return best if haversine(point, best) <= SNAP_LIMIT_M else None


def route(graph, start, goal, limit_m: float) -> list[tuple[float, float]] | None:
    """Dijkstra mit Abbruch, sobald das Ziel gezogen ist oder der Umweg zu groß wird.

    ponytail: kein A*. Die Abschnitte sind ein paar Kilometer lang, da kostet die
    Heuristik mehr Code als sie spart.
    """
    dist = {start: 0.0}
    prev: dict[tuple[float, float], tuple[float, float]] = {}
    queue = [(0.0, start)]
    while queue:
        d, node = heapq.heappop(queue)
        if node == goal:
            path = [node]
            while node in prev:
                node = prev[node]
                path.append(node)
            return path[::-1]
        if d > dist.get(node, math.inf) or d > limit_m:
            continue
        for neighbour in graph[node]:
            nd = d + haversine(node, neighbour)
            if nd < dist.get(neighbour, math.inf):
                dist[neighbour] = nd
                prev[neighbour] = node
                heapq.heappush(queue, (nd, neighbour))
    return None


def in_region(point) -> bool:
    return REGION[0] <= point[0] <= REGION[2] and REGION[1] <= point[1] <= REGION[3]


def rail_segments() -> dict[str, list[list[float]]]:
    """Alle geraden Zug-Abschnitte im Kartenausschnitt, nach Haltestellenpaar."""
    raw = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    meta = raw.get("line_meta", {})
    out: dict[str, list[list[float]]] = {}
    for line, paths in raw.get("line_paths", {}).items():
        if meta.get(line, {}).get("category") not in RAIL_CATEGORIES:
            continue
        for key, path in paths.items():
            if len(path) == 2 and all(in_region(p) for p in path):
                out[key] = path
    return out


def main() -> None:
    segments = rail_segments()
    print(f"{len(segments)} gerade Zug-Abschnitte im Ausschnitt", flush=True)
    graph = build_graph(fetch_osm())
    print(f"{len(graph)} Netzknoten", flush=True)
    index = build_index(graph)

    resolved: dict[str, list[list[float]]] = {}
    skipped = {"snap": 0, "route": 0, "detour": 0}
    for done, (key, (start_pt, end_pt)) in enumerate(sorted(segments.items()), 1):
        a, b = snap(tuple(start_pt), index), snap(tuple(end_pt), index)
        if a is None or b is None:
            skipped["snap"] += 1
        elif a == b:
            skipped["route"] += 1
        else:
            straight = haversine(a, b)
            path = route(graph, a, b, straight * DETOUR_LIMIT)
            if path is None:
                skipped["route"] += 1
            else:
                length = sum(haversine(u, v) for u, v in zip(path, path[1:]))
                if length > straight * DETOUR_LIMIT:
                    skipped["detour"] += 1
                else:
                    # Die echten Haltekoordinaten behalten, sonst springt die Linie am Bahnsteig.
                    resolved[key] = [list(start_pt)] + [[lat, lon] for lat, lon in path] + [list(end_pt)]
        if done % 50 == 0:
            print(f"  {done}/{len(segments)} · {len(resolved)} gelöst", flush=True)

    RAIL_FILE.write_text(json.dumps(resolved), encoding="utf-8")
    print(f"{len(resolved)} Abschnitte nach {RAIL_FILE.name}; übersprungen: {skipped}")


if __name__ == "__main__":
    main()

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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kvb_hafas.parsing import station_ext_id  # noqa: E402  (erst nach dem Pfad-Setup importierbar)
GEOMETRY_FILE = ROOT / "map_geometry.json"
RAIL_FILE = ROOT / "rail_geometry.json"
OSM_CACHE_DIR = ROOT / ".cache"

# Nur der Kartenausschnitt plus Rand. Die gecachten Abschnitte reichen von Paris
# bis Berlin (ICE, THA) — dafür ein Schienennetz zu routen wäre absurd, und
# sichtbar ist davon ohnehin nichts.
# Nur der Kartenausschnitt plus Rand. Die gecachten Abschnitte reichen von Paris
# bis Berlin (ICE, THA) — dafür ein Schienennetz zu routen wäre absurd, und
# sichtbar ist davon ohnehin nichts.
REGION = (50.82, 6.72, 51.08, 7.22)  # min_lat, min_lon, max_lat, max_lon
TILE = (0.13, 0.25)  # Kachelgröße in Grad; ein Köln-Ausschnitt dieser Größe kostet ~10 s
RAIL_CATEGORIES = {"S-Bahn", "RE", "RB", "Regio", "ICE", "IC", "Fern", "THA"}  # kein SEV: Schienenersatz fährt Straße
# Nur Eisenbahn. `light_rail` ist in Köln die KVB-Stadtbahn, ein eigenes Netz: S-Bahn-Halte
# snappen sonst auf ein Stadtbahngleis acht Meter daneben, und die Route zwischen zwei
# Halten 1 km auseinander geht 22 km außen herum. Stadtbahn und Bus bekommen ihre
# Verläufe ohnehin von HAFAS.
RAIL_TYPES = {"rail"}
# Schienenersatzverkehr fährt Straße. Wohnstraßen sind dabei, weil Ersatzhalte oft
# nicht an der Hauptstraße liegen; Feld- und Fußwege bleiben draußen.
ROAD_TYPES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")
SNAP_LIMIT_M = 250.0  # weiter weg heißt: der Halt liegt nicht an diesem Netz
DETOUR_LIMIT = 3.5  # Route länger als das Vielfache der Luftlinie -> falsch gesnappt (2.5 verwarf 9 echte Bögen)
CELL = 0.01  # ~1 km Rasterzelle für die Nachbarsuche
REACH_M = 60000.0  # so weit wird einem Ziel außerhalb des Ausschnitts gefolgt
MIN_PROGRESS_M = 300.0  # weniger Annäherung ans Ziel lohnt den Verlauf nicht

# overpass.osm.ch ist ein reiner Schweiz-Auszug und antwortet für Köln leer — nicht
# in die Liste aufnehmen, sonst landet eine leere Antwort im Cache.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
# Bewusst ohne Regex und ohne `["service"!~"."]`: mit diesen Filtern laufen die
# öffentlichen Instanzen in einen 504, ohne sie antwortet dieselbe Fläche in ~10 s.
# Gefiltert wird stattdessen hier (siehe build_graph()).
QUERY_RAIL = '[out:json][timeout:180];way["railway"]({0},{1},{2},{3});out geom;'
# Für Straßen keine Sammelabfrage über alle `highway`-Werte: das wären mit Fuß- und
# Wirtschaftswegen ein Vielfaches an Daten. Exakte Werte sind zudem indexgestützt.
QUERY_ROAD = "[out:json][timeout:180];(" + "".join(
    f'way["highway"="{t}"]({{0}},{{1}},{{2}},{{3}});' for t in ROAD_TYPES
) + ");out geom;"


def tiles() -> list[tuple[float, float, float, float]]:
    """Region in Kacheln zerlegen — eine Anfrage über alles wird zu teuer."""
    out = []
    lat = REGION[0]
    while lat < REGION[2]:
        lon = REGION[1]
        while lon < REGION[3]:
            out.append((lat, lon, min(lat + TILE[0], REGION[2]), min(lon + TILE[1], REGION[3])))
            lon += TILE[1]
        lat += TILE[0]
    return out


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Entfernung zweier (lat, lon)-Punkte in Metern."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def fetch_tile(box: tuple[float, float, float, float], query: str, prefix: str) -> dict:
    """Eine Kachel holen, Antwort auf Platte cachen.

    Eine leere Antwort wird nicht gecacht — ein Spiegel mit anderem Auszug würde
    sonst dauerhaft ein leeres Netz festschreiben.
    """
    cache = OSM_CACHE_DIR / ("%s_%.2f_%.2f.json" % (prefix, box[0], box[1]))
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    data = urllib.parse.urlencode({"data": query.format(*box)}).encode()
    for url in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "kvb-hafas-client/0.1"})
            with urllib.request.urlopen(req, timeout=300) as res:
                raw = res.read()
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"  {url}: {err}", flush=True)
            continue
        osm = json.loads(raw)
        if not osm.get("elements"):
            print(f"  {url}: leere Antwort, übersprungen", flush=True)
            continue
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(raw)
        return osm
    sys.exit("Kein Overpass-Spiegel erreichbar — später nochmal.")


def fetch_osm(query: str, prefix: str) -> list[dict]:
    """Alle Kacheln der Region, nacheinander (die Instanzen mögen keine Parallelität)."""
    boxes = tiles()
    out = []
    for n, box in enumerate(boxes, 1):
        print(f"Kachel {prefix} {n}/{len(boxes)} {box} …", flush=True)
        out.append(fetch_tile(box, query, prefix))
        time.sleep(1.0)  # die öffentlichen Instanzen sind ein Gemeingut
    return out


def build_graph(tiles_osm: list[dict], tag: str = "railway", types=RAIL_TYPES, with_service: bool = False) -> dict[tuple[float, float], list[tuple[float, float]]]:
    """Wege zu einem Knotengraph verknüpfen; gemeinsame Koordinaten sind gemeinsame Knoten.

    Hier wird auch gefiltert, was die Overpass-Abfrage bewusst nicht filtert:
    nur Eisenbahn und S-Bahn-Gleise, keine stillgelegten Strecken. Betriebsgleise
    (`service`: Abstellung, Rangierfahrt, Bahnhofsvorfeld) bleiben normalerweise
    draußen — sonst schnappt die Suche im Bahnhof auf das erstbeste Nebengleis.
    Für Halte, zwischen denen es sonst gar keine Verbindung gibt, wird der Graph
    ein zweites Mal mit ihnen gebaut (siehe main()).
    """
    graph: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for osm in tiles_osm:
        for way in osm.get("elements", []):
            tags = way.get("tags", {})
            if tags.get(tag) not in types or (tag == "railway" and tags.get("service") and not with_service):
                continue
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


def station_key(key: str) -> str:
    """Mast-Paar zu Haltestellen-Paar normalisieren.

    JourneyDetails referenziert Halte über Mast-IDs, und die unterscheiden sich
    je Fahrt und Bahnsteig. Auf Mastebene gespeicherte Verläufe passen deshalb
    nach jedem Neustart auf immer neue Paare nicht mehr. Der Verlauf zwischen
    zwei Haltestellen ist aber derselbe, egal an welchem Bahnsteig er beginnt.
    """
    return "|".join(station_ext_id(part) or part for part in key.split("|", 1))


def rail_segments(categories=RAIL_CATEGORIES) -> dict[str, list[list[float]]]:
    """Gerade Zug-Abschnitte, die den Kartenausschnitt berühren.

    Ein Ende reicht: Fahrten nach Düsseldorf oder Aachen ziehen ihre Luftlinie
    sonst quer durch die halbe Karte. Der Teil im Netz wird geroutet, der Rest
    bleibt gerade — der liegt ohnehin außerhalb.
    """
    raw = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    meta = raw.get("line_meta", {})
    out: dict[str, list[list[float]]] = {}
    for line, paths in raw.get("line_paths", {}).items():
        if meta.get(line, {}).get("category") not in categories:
            continue
        for key, path in paths.items():
            if len(path) == 2 and any(in_region(p) for p in path):
                out.setdefault(station_key(key), path)
    return out


def resolve(graph, index, start_pt, end_pt) -> list[list[float]] | None:
    """Verlauf zwischen zwei Halten, so weit das lokale Netz reicht.

    Liegen beide Halte im Netz, ist das eine normale Route. Liegt nur einer drin,
    wird vom ihm aus gesucht und der Knoten genommen, der die Gesamtstrecke
    (gefahren + Restluftlinie) am kleinsten macht — also der Punkt, an dem das
    Gleis den geladenen Ausschnitt in Richtung Ziel verlässt.
    """
    a, b = snap(tuple(start_pt), index), snap(tuple(end_pt), index)
    flip = a is None
    if flip:
        a, b, start_pt, end_pt = b, a, end_pt, start_pt
    if a is None:
        return None
    straight = haversine(tuple(start_pt), tuple(end_pt))
    if b is not None and a != b:
        path = route(graph, a, b, straight * DETOUR_LIMIT)
        if path is None:
            return None
        length = sum(haversine(u, v) for u, v in zip(path, path[1:]))
        if length > straight * DETOUR_LIMIT:
            return None
    else:
        path = route_towards(graph, a, tuple(end_pt))
        if path is None:
            return None
    full = [list(start_pt)] + [[lat, lon] for lat, lon in path] + [list(end_pt)]
    return full[::-1] if flip else full


def route_towards(graph, start, goal_pt) -> list[tuple[float, float]] | None:
    """Vom Startknoten aus dem Ziel entgegen, bis das geladene Netz endet.

    Dijkstra ohne festes Ziel, gedeckelt auf REACH_M. Genommen wird der Knoten,
    der dem Ziel am nächsten kommt — aber nur, solange die gefahrene Strecke
    höchstens DETOUR_LIMIT-mal so lang ist wie die zurückgelegte Annäherung.
    Nach `gefahren + Restluftlinie` zu bewerten funktioniert hier nicht: Gleise
    sind immer länger als die Luftlinie, damit gewönne stets der Startpunkt.
    """
    dist = {start: 0.0}
    prev: dict[tuple[float, float], tuple[float, float]] = {}
    origin = haversine(start, goal_pt)
    best, best_remaining = None, origin
    queue = [(0.0, start)]
    while queue:
        d, node = heapq.heappop(queue)
        if d > dist.get(node, math.inf) or d > REACH_M:
            continue
        remaining = haversine(node, goal_pt)
        if remaining < best_remaining and d <= DETOUR_LIMIT * (origin - remaining):
            best, best_remaining = node, remaining
        for neighbour in graph[node]:
            nd = d + haversine(node, neighbour)
            if nd < dist.get(neighbour, math.inf):
                dist[neighbour] = nd
                prev[neighbour] = node
                heapq.heappush(queue, (nd, neighbour))
    if best is None or origin - best_remaining < MIN_PROGRESS_M:
        return None  # das Netz führt nicht in die Richtung — gerade Linie ist ehrlicher
    path = [best]
    while best in prev:
        best = prev[best]
        path.append(best)
    return path[::-1]


def main() -> None:
    # Frühere Läufe behalten: map_geometry.json trägt inzwischen die aufgelösten
    # Verläufe selbst, die Halte-Paare tauchen dort also nicht mehr als gerade auf.
    try:
        resolved = json.loads(RAIL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        resolved = {}
    # Ältere Dateien lagen auf Mastebene — einmalig auf Haltestellen umschlüsseln.
    resolved = {station_key(key): path for key, path in resolved.items()}
    print(f"{len(resolved)} Abschnitte aus früheren Läufen", flush=True)

    todo = {"Schiene": rail_segments(), "Straße": rail_segments({"SEV"})}
    for what, segments in todo.items():
        todo[what] = {key: path for key, path in segments.items() if key not in resolved}
        print(f"{len(todo[what])} gerade Abschnitte auf {what}", flush=True)

    graphs = {}
    if todo["Schiene"]:
        rail_tiles = fetch_osm(QUERY_RAIL, "rail")
        graph = build_graph(rail_tiles)
        # Rückfallebene für Halte, die nur über Bahnhofs- und Betriebsgleise zusammenhängen.
        yard = build_graph(rail_tiles, with_service=True)
        print(f"{len(graph)} Schienenknoten ({len(yard)} mit Betriebsgleisen)", flush=True)
        graphs["Schiene"] = [(graph, build_index(graph)), (yard, build_index(yard))]
    if todo["Straße"]:
        # Schienenersatzverkehr fährt Straße — auf Gleise gesnappt wäre der Verlauf hübsch,
        # aber falsch.
        road = build_graph(fetch_osm(QUERY_ROAD, "road"), tag="highway", types=ROAD_TYPES)
        print(f"{len(road)} Straßenknoten", flush=True)
        graphs["Straße"] = [(road, build_index(road))]

    skipped = 0
    for what, segments in todo.items():
        for done, (key, (start_pt, end_pt)) in enumerate(sorted(segments.items()), 1):
            for graph, index in graphs[what]:
                path = resolve(graph, index, start_pt, end_pt)
                if path is not None:
                    resolved[key] = path
                    break
            else:
                skipped += 1
            if done % 50 == 0:
                print(f"  {what} {done}/{len(segments)} · {len(resolved)} gelöst", flush=True)

    RAIL_FILE.write_text(json.dumps(resolved), encoding="utf-8")
    print(f"{len(resolved)} Abschnitte nach {RAIL_FILE.name}; {skipped} bleiben gerade")


if __name__ == "__main__":
    main()

# kvb-hafas-client

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Tests: mocked HTTP](https://img.shields.io/badge/tests-mocked%20HTTP-brightgreen.svg)](tests/test_client.py)

Inoffizieller Python-Client für die Echtzeit-Fahrplandaten der **KVB** (Kölner
Verkehrs-Betriebe AG), Köln — Haltestellensuche, Live-Abfahrten, Verbindungen
und Störungsmeldungen, ohne offizielle API.

Live-Karte, Departures-Board und Stats-Dashboard laufen direkt hier mit
(`uv run map_server.py`, siehe unten). Als Home Assistant Add-on gibt es dafür
zusätzlich [kvb-ha-map](https://github.com/christoph-teichmeister/kvb-ha-map)
— eine dünne Docker/Ingress-Hülle, die genau diesen Server importiert.

## Hintergrund

Die KVB betreibt keine offizielle öffentliche API. Dieses Projekt spricht
stattdessen den **HAFAS**-Backend-Endpoint an, den der offizielle
[Widget-Generator](https://www.kvb.koeln/fahrtinfo/widget-generator/index.html)
im Browser lädt:

- Endpoint: `https://auskunft.kvb.koeln/gate` (HAFAS `mgate`-Protokoll)
- Auth: `{"type": "AID", "aid": "Rt6foY5zcTTRXMQs"}` — kein Checksum/Salt nötig

Beides steht im öffentlich ausgelieferten Frontend; keine Credentials umgangen,
keine Authentifizierung "geknackt". Fundkette: [docs/API.md#herkunft](docs/API.md#herkunft).

## ⚠️ Rechtlicher Hinweis

- **Nicht offiziell von der KVB freigegeben oder unterstützt.**
- Nutzung auf eigenes Risiko. Der Endpoint kann sich jederzeit ändern oder
  gesperrt werden.
- **Nicht kommerziell nutzen.**
- **Rate-Limiting einhalten** — keine Lastspitzen erzeugen, das ist
  Produktions-Infrastruktur der KVB.
- Kein KVB-Branding/Logo in eigenen Anwendungen verwenden.
- Bei Zweifeln: KVB direkt kontaktieren und um offiziellen Zugang bitten.

## Installation

Das Projekt nutzt [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Nutzung

```python
from kvb_hafas import KVBHafasClient

client = KVBHafasClient()

stops = client.find_stops("Neumarkt")
print(stops[0])  # Stop(name='Köln Neumarkt', ext_id='900000002', ...)

for dep in client.station_board(stops[0].ext_id):
    print(dep.planned, dep.realtime, dep.line, dep.direction, dep.cancelled)
```

Interaktive CLI — Abfahrten, Verbindungssuche (inkl. Detailansicht mit späteren
Alternativen, straßengenauen Fußwegen und Echtzeit-Nachladen), Umkreissuche,
Störungsmeldungen, Isochrone, Live-Fahrzeuge, Liniendetails, Serverinfo:

```bash
uv run main.py
```

Live-Karte, Haltestellen-Board und Verspätungs-Statistiken im Browser:

```bash
uv run map_server.py
```

Startet auf `http://localhost:8099` — Vehicle-/Alert-/Netz-/Stops-Endpunkte,
Geometrie-Prefetch und SQLite-Vehicle-History laufen komplett hier
(`kvb_hafas/server/`), keine externen Dependencies über `requests` hinaus.
Konfiguration über Env-Vars (`PORT`, `HISTORY_ENABLED`,
`HISTORY_SAMPLE_INTERVAL_SECONDS`, `VEHICLE_POLL_CACHE_TTL_SECONDS`,
`DEFAULT_MAP_CENTER_LAT`/`_LON`, `TILE_URL`/`TILE_ATTRIBUTION`,
`VEHICLE_SNAPSHOT_MIN_LAT`/`_LON`/`MAX_LAT`/`_LON` (stadtweite Bbox, die ein
Hintergrund-Thread unabhängig von offenen Karten-Tabs pollt — versorgt
Dashboard-Live-Stats und die Verlaufs-Erfassung), …) — Defaults siehe
`kvb_hafas/server/http_server.py`.

Das [kvb-ha-map](https://github.com/christoph-teichmeister/kvb-ha-map) Home
Assistant Add-on ist nur noch eine dünne Docker/Ingress-Hülle darum: es zieht
diese Library als Dependency und startet
`python -m kvb_hafas.server.http_server` direkt — kein eigener Server- oder
UI-Code mehr dort, damit UI/Server nur an einer Stelle gepflegt werden.

Gleise der DB-Produkte liegen fertig geroutet in `data/rail_geometry.json` im
Repo — neu holen per `uv run tools/fetch_rail_geometry.py` nur bei
Netzänderungen. Diese Datei ist aus OpenStreetMap-Daten abgeleitet und steht
unter der [ODbL](https://opendatacommons.org/licenses/odbl/) — ©
OpenStreetMap-Mitwirkende.

> ⚠️ Der `ani`-Track gibt `proc` in **Prozent** an, nicht in Promille.

Die Library hängt nur an `requests`. Die CLI zusätzlich an `questionary` und
`rich` — beide in der `dev`-Dependency-Group, nicht Teil des Pakets.

## Projektstruktur

```
kvb_hafas/      Library (nur requests)
  models.py       Dataclasses: Stop, Departure, Connection, JourneyRoute, …
  parsing.py      HAFAS-Rohdaten-Helfer (Polyline, Mast-/Haltestellen-IDs)
  client.py       KVBHafasClient — alle Requests gegen den mgate-Endpunkt
  storage.py      SQLite-Schema und Persistenz für Laufwege
cli/            Interaktive Terminal-Oberfläche (questionary + rich)
  ui.py           Prompts, Auswahlmenüs, Panels
  format.py       HAFAS-Zeiten/Dauern -> lesbare Strings
  departures.py / trips.py / alerts.py / geo.py / network.py   je ein Menüpunkt
timetable/      Fahrplan-Erhebung: fetch.py (einsammeln), analyze.py + analyze.sql (auswerten)
main.py         Entry-Point der CLI
map_server.py   Entry-Point des Web-UI-Servers (uv run map_server.py)
kvb_hafas/server/  HTTP-Server für die Web-UI (auch von kvb-ha-map importiert)
  http_server.py  Handler + main(): Vehicle-/Alert-/Netz-/Stops-/Departures-Endpunkte, Geometrie-Prefetch
  history_store.py  SQLite-Zeitreihe für Fahrzeugpositionen
  stats.py          Live- und historische Verspätungsstatistiken
kvb_hafas/webui/  UI-Assets (Package-Daten)
  index.html, map.html, departures.html, dashboard.html, shared.css
  is_kvb_local.py   Bucketing-Helfer: lokale (Tram/Bus) vs. durchfahrende Linien
  vendor/           Leaflet 1.9.4 lokal (leaflet.js/css + Marker-Images)
```

Fahrplan eines Betriebstags einsammeln und auswerten:

```bash
uv run -m timetable.fetch --line 5 --date 2026-09-17 --db timetable.db
uv run -m timetable.analyze --db timetable.db
```

## Was funktioniert

Abgedeckt sind Haltestellensuche und -details, Echtzeit-Abfahrten,
Verbindungssuche inkl. Alternativen und Tarifstufe, Störungsmeldungen,
Isochrone, Live-Fahrzeugpositionen, Streckenverläufe und der komplette
Linienkatalog — 3125 Linien, davon 710 `de:vrs` (Köln/Bonn).

Nicht zu holen: **historische Daten** jeder Art (kein Archiv) und **Auslastungsdaten** (`tcocL` & Co. kommen leer
zurück). Push-Abos (`Subscr*`)
bräuchten ein Nutzerkonto und sind als einzige schreibende Methoden bewusst
nicht angefasst.

**Vollständige API-Referenz mit allen Feldern, Beispiel-Requests und
Fehlercodes:** [docs/API.md](docs/API.md) — inklusive
[Inventar aller 47 existierenden HAFAS-Methoden](docs/API.md#methoden-inventar)
mit Status, Client-Abdeckung und Fehlercode auf leeren Request.

Dieselbe Referenz als OpenAPI-Spec zum Durchklicken (Swagger UI):
**[christoph-teichmeister.github.io/kvb-hafas-client](https://christoph-teichmeister.github.io/kvb-hafas-client/)**
— Quelle: [docs/openapi.yaml](docs/openapi.yaml). „Try it out" ist bewusst
deaktiviert (CORS, und es ist KVB-Produktions-Infrastruktur).

## Tests

```bash
uv run pytest tests/ -v
```

Tests laufen komplett gegen gemockte HTTP-Responses — keine Live-Calls gegen
den KVB-Server nötig.

## Nächste Schritte / Ideen

- Ist-Zeiten mitschreiben: `timetable/` erfasst bisher nur den Soll-Fahrplan (`stop_time` kennt keine Echtzeit-Spalte).
  Ein wiederkehrender
  `station_board()`-Lauf in dieselbe DB wäre die Grundlage für eine eigene
  Zeitreihe.
- Darauf aufbauend eine eigene Auslastungs-/Pünktlichkeits-Heuristik: Ist- vs.
  Soll-Abweichung über Zeit als Proxy für Verspätungshäufigkeit.

## Mitwirken

Issues und Pull Requests sind willkommen. Vor größeren Änderungen bitte
erst ein Issue aufmachen, um das Vorgehen abzustimmen. Neue Features sollten
mit Tests gegen gemockte HTTP-Responses abgesichert sein (`tests/test_client.py`).

## Lizenz

[MIT](LICENSE) — siehe Lizenztext für Details. Beachte trotzdem den
[rechtlichen Hinweis](#️-rechtlicher-hinweis) oben: die MIT-Lizenz betrifft nur
den Code, nicht die Nutzungsbedingungen des KVB-Endpoints.

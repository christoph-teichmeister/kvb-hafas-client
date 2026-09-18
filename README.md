# kvb-hafas-client

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Tests: mocked HTTP](https://img.shields.io/badge/tests-mocked%20HTTP-brightgreen.svg)](tests/test_client.py)

Inoffizieller Python-Client für die Echtzeit-Fahrplandaten der **KVB** (Kölner
Verkehrs-Betriebe AG), Köln — Haltestellensuche, Live-Abfahrten, Verbindungen
und Störungsmeldungen, ohne offizielle API.

Ein Kartenfrontend, das diesen Client nutzt, gibt es unter
[kvb-ha-map](https://github.com/christoph-teichmeister/kvb-ha-map).

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

Live-Karte im Browser — alle Fahrzeuge im Kartenausschnitt, animiert aus den
`ani`-Tracks von `vehicle_positions()`:

```bash
uv run map_server.py   # -> http://localhost:8000
```

`map_server.py` (nur stdlib) ist ein dünner Proxy vor `vehicle_positions()`,
`map.html` nutzt Leaflet aus `vendor/` (lokal, damit die Karte ohne CDN-Roundtrip
sofort steht) und OpenStreetMap-Tiles vom CDN. Ausschnitt folgt der Karte,
Polling alle 15 s (Track reicht 120 s), Antworten 15 s gecacht.

Fahrzeuge fahren auf echtem Streckenverlauf statt Luftlinie, dazu
Haltestellen, Linienfarben, Verspätungen, Störungen und Filter nach Linie und
Verkehrsmittel. Der Verlauf wird im Hintergrund nachgeladen und in
`data/map_geometry.json` abgelegt, nach einem Neustart steht das Netz sofort;
Details dazu stehen in `map_server.py`. Gleise der DB-Produkte liegen fertig
geroutet in `data/rail_geometry.json` im Repo — neu holen per
`uv run tools/fetch_rail_geometry.py` nur bei Netzänderungen. Diese Datei ist aus
OpenStreetMap-Daten abgeleitet und steht unter der
[ODbL](https://opendatacommons.org/licenses/odbl/) — © OpenStreetMap-Mitwirkende.

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
map_server.py   stdlib-Server für die Live-Karte
map.html        Live-Karte (Leaflet, pollt map_server.py)
vendor/         Leaflet 1.9.4 lokal (leaflet.js/css + Marker-Images)
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

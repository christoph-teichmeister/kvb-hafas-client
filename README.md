# kvb-hafas-client

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Tests: mocked HTTP](https://img.shields.io/badge/tests-mocked%20HTTP-brightgreen.svg)](tests/test_client.py)

Inoffizieller Python-Client für die Echtzeit-Fahrplandaten der **KVB** (Kölner
Verkehrs-Betriebe AG), Köln — Haltestellensuche, Live-Abfahrten, Verbindungen
und Störungsmeldungen, ohne offizielle API.

## Inhalt

- [Hintergrund](#hintergrund)
- [⚠️ Rechtlicher Hinweis](#️-rechtlicher-hinweis)
- [Installation](#installation)
- [Nutzung](#nutzung)
- [Projektstruktur](#projektstruktur)
- [Was funktioniert](#was-funktioniert)
- [Tests](#tests)
- [Nächste Schritte / Ideen](#nächste-schritte--ideen)
- [Mitwirken](#mitwirken)
- [Lizenz](#lizenz)

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

- **Streckenverlauf statt Luftlinie** — je Linie und Richtung einmal
  `journey_segments_many()`, zu zehnt gebündelt im Hintergrund geholt und in
  `map_geometry.json` gespeichert; nach einem Neustart steht das Netz sofort,
  beim allerersten Lauf fährt der Rest solange auf der Luftlinie. Für DB-Produkte (S-Bahn, RE/RB, IC/ICE) liefert HAFAS
  nur die Halte selbst — deren Gleise holt
  `uv run tools/fetch_rail_geometry.py` einmalig aus OpenStreetMap nach
  `rail_geometry.json`.
- **Verspätung** — Minuten am nächsten Halt aus `stopL`; ab 3 min gelber Rand
  und Minuten im Label.
- **Linienfarben** — direkt aus HAFAS (`prodL[].icoX` → `common.icoL[].bg`).
  Ausnahme: DB-Produkte melden durchweg `#ffffff`, für die gibt es eine
  Ersatzfarbe je Produktgruppe.
- **Streckennetz** — Schalter „Strecken" zeichnet die bekannten Abschnitte je
  Linie in Linienfarbe (`/api/network`), Filter gilt auch dafür.
- **Filter** — nach Linien (`1,9,18`), nach Verkehrsmittel, nur Verspätete.
- **Störungen** — `service_alerts()` mit Koordinaten aus Loc-Referenzen und
  `himMsgEdgeL[].icoCrd`; Meldungen ohne Geo-Bezug landen in der Liste rechts.

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

| Feature                                                          | Status                                                                                                   |
|------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Haltestellensuche (`find_stops`)                                 | ✅                                                                                                       |
| Umkreissuche (`nearby_stops`)                                    | ⚠️ liefert auch POIs, kein reiner Stop-Filter                                                            |
| Echtzeit-Abfahrten (`station_board`)                             | ✅ inkl. Soll/Ist-Zeiten                                                                                 |
| Fahrtausfälle (`isCncl`-Flag)                                    | ⚠️ vorhanden, nicht gegen echten Ausfall verifiziert                                                     |
| Zwischenhalte einer Fahrt (`journey_details`)                    | ✅                                                                                                       |
| Verbindungssuche (`trip_search`)                                 | ✅                                                                                                       |
| Verbindung wiederherstellen (`reconstruct`)                      | ✅ via `Connection.ctx_recon`, frische Echtzeit ohne neue Suche                                          |
| Alternativen zu einer Verbindung (`trip_alternatives`)           | ✅ via `Connection.ctx_recon`, spätere Verbindungen derselben Relation                                   |
| Fußweg straßengenau (`walk_route`)                               | ⚠️ nur zu Fußweg-Abschnitten aus `trip_search`; kein freies A-nach-B-Routing                             |
| Störungsmeldungen (`service_alerts`)                             | ✅ netzweit; `line=` serverseitig, `stop=` client-seitig. `category == 99` = Werbung, selbst rausfiltern |
| Aktuell betroffene Haltestellen (`affected_stops`)               | ✅ parameterlos                                                                                          |
| Störungen im Kartenausschnitt (`alerts_in_area`)                 | ⚠️ funktioniert, war für Köln aber immer leer                                                            |
| Haltestellen-Detail + alle Linien (`stop_details`, `stop_lines`) | ✅ aus dem Fahrplan, nicht nur die nächsten Abfahrten                                                    |
| Linien im Umkreis (`lines_in_area`)                              | ✅ vollständiger als `stop_details().lines` (inkl. Nachtlinien); Deckel bei 50                           |
| Isochrone (`reachable_stops`)                                    | ✅ Steige werden auf die Haltestelle zusammengefasst                                                     |
| Live-Fahrzeugpositionen (`vehicle_positions`)                    | ✅ inkl. Animations-Track, Verspätung, nächster Halt — hochgerechnet, kein GPS                           |
| Streckenverlauf je Haltestellenpaar (`journey_segments`)         | ✅ viel feiner als `journey_course()`                                                                    |
| Linienverlauf als Polyline (`journey_course`)                    | ✅ ein Punkt pro Halt, Google-Encoded-Polyline                                                           |
| Offizielle Linienfarben                                          | ✅ aus `common.icoL`                                                                                     |
| Liniensuche & -details (`find_lines`, `line_details`)            | ⚠️ inkl. Betreiber und Fahrtenzahl; Pünktlichkeitsstatistik von KVB nicht befüllt                        |
| Kompletter Linienkatalog (`all_lines`)                           | ✅ 3125 Linien in einem Request, davon 710 `de:vrs` (Köln/Bonn)                                          |
| Fahrten einer Linie inkl. Verkehrstage (`find_journeys`)         | ✅ `sDaysI` im Klartext                                                                                  |
| Fahrplanperiode / Serverzeit (`server_info`)                     | ✅                                                                                                       |
| Push-Abos (`Subscr*`)                                            | ⚠️ brauchen ein Nutzerkonto; einzige schreibende Methoden, bewusst nicht angefasst                       |
| Historische Daten jeder Art                                      | ❌ kein Archiv — [Details](docs/API.md#historische-daten)                                                |
| Auslastungsdaten                                                 | ❌ `tcocL` & Co. kommen leer zurück                                                                      |
| Verbindungs-Optionen (`num`, `via_ext_id`, `products`)           | ✅ Anzahl, Zwischenhalt, Verkehrsmittelfilter über `kvb_hafas.PRODUCTS`                                  |
| Früher/später blättern (`trip_page`)                             | ✅ `ctx_earlier`/`ctx_later`, wie das „früher/später" der offiziellen Auskunft                           |
| Tarife/Preise (`Connection.fare_cents`)                          | ✅ Rheinlandtarif-Preisstufe, kommt gratis mit jeder `trip_search()`                                     |

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

- Streckenverläufe zwischen Serverstarts persistieren (sie ändern sich nur mit
  dem Fahrplan) statt den Cache jedes Mal neu zu füllen.
- Persistenz/Zeitreihen für eigene Auslastungs-Heuristik (z.B. Ist- vs.
  Soll-Abweichung über Zeit als Proxy für Verspätungshäufigkeit).

## Mitwirken

Issues und Pull Requests sind willkommen. Vor größeren Änderungen bitte
erst ein Issue aufmachen, um das Vorgehen abzustimmen. Neue Features sollten
mit Tests gegen gemockte HTTP-Responses abgesichert sein (`tests/test_client.py`).

## Lizenz

[MIT](LICENSE) — siehe Lizenztext für Details. Beachte trotzdem den
[rechtlichen Hinweis](#️-rechtlicher-hinweis) oben: die MIT-Lizenz betrifft nur
den Code, nicht die Nutzungsbedingungen des KVB-Endpoints.

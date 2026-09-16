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
- [Was funktioniert](#was-funktioniert)
- [Tests](#tests)
- [Nächste Schritte / Ideen](#nächste-schritte--ideen)
- [Mitwirken](#mitwirken)
- [Lizenz](#lizenz)

## Hintergrund

Die KVB betreibt keine offizielle öffentliche API. Dieses Projekt spricht
stattdessen den **HAFAS**-Backend-Endpoint an, den der offizielle
[Widget-Generator](https://www.kvb.koeln/fahrtinfo/widget-generator/index.html)
der KVB im Browser lädt:

- Endpoint: `https://auskunft.kvb.koeln/gate` (HAFAS `mgate`-Protokoll)
- Auth: `{"type": "AID", "aid": "Rt6foY5zcTTRXMQs"}` — kein Checksum/Salt nötig

Gefunden durch Verfolgen der öffentlich erreichbaren Assets der
Widget-Generator-Seite (`hafas_webapp_config.js` → `config/webapp.config.json`
→ `urlMgate`). Keine Credentials umgangen, keine Authentifizierung
"geknackt" — die aid ist Teil des öffentlich ausgelieferten Frontends.

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

Oder interaktiv über die CLI (Menü mit Abfahrten, Verbindungssuche, Umkreissuche,
Störungsmeldungen, Isochrone, Live-Fahrzeugen, Liniendetails und Serverinfo).
Nach einer Verbindungssuche lässt sich jede Verbindung im Detail weiterverfolgen:
spätere Alternativen, Fußwege straßengenau, Echtzeit nachladen.

```bash
uv run main.py
```

Die Library selbst hängt nur an `requests`. Die CLI nutzt zusätzlich `questionary`
(Pfeiltasten-Menüs) und `rich` (Tabellen, Farben) — beide liegen in der
`dev`-Dependency-Group und sind nicht Teil des Pakets.

## Was funktioniert

| Feature                                                    | Status                                                                                                                                                                                                                         |
|------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Haltestellensuche (`find_stops`)                           | ✅                                                                                                                                                                                                                             |
| Umkreissuche (`nearby_stops`)                              | ✅ liefert aber auch POIs, kein reiner Stop-Filter                                                                                                                                                                             |
| Echtzeit-Abfahrten (`station_board`)                       | ✅ inkl. Soll/Ist-Zeiten                                                                                                                                                                                                       |
| Fahrtausfälle (`isCncl`-Flag)                              | ✅ vorhanden, noch nicht gegen echten Ausfall verifiziert                                                                                                                                                                      |
| Zwischenhalte einer Fahrt (`journey_details`)              | ✅                                                                                                                                                                                                                             |
| Verbindungssuche (`trip_search`)                           | ✅                                                                                                                                                                                                                             |
| Störungsmeldungen (`service_alerts`)                       | ✅ netzweit, inkl. Baustellen/Aufzugsausfälle — `line="133"` filtert serverseitig, `stop=…` client-seitig (Loc-Referenz + Namensabgleich im Text). Die eingestreute KVB-Werbung (`category == 99`) musst du selbst rausfiltern |
| Haltestellen-Detail + alle Linien (`stop_details`, `stop_lines`) | ✅ aus dem Fahrplan (`LocDetails.pRefL`), nicht nur die nächsten Abfahrten                                                                                                                                              |
| Isochrone „was ist in X Minuten erreichbar“ (`reachable_stops`) | ✅ Steige werden auf die Haltestelle zusammengefasst                                                                                                                                                                    |
| Live-Fahrzeugpositionen (`vehicle_positions`)              | ✅ Bounding-Box, Positionen sind aus Fahrplan + Prognose hochgerechnet, kein GPS                                                                                                                                               |
| Liniensuche & -details (`find_lines`, `line_details`)      | ✅ inkl. Betreiber und Fahrtenzahl; das Schema für Pünktlichkeitsstatistik ist da, aber von der KVB nicht befüllt                                                                                                              |
| Fahrten einer Linie inkl. Verkehrstage (`find_journeys`)   | ✅ `sDaysI` im Klartext („Mo - Fr; nicht 10. bis 28. Aug“)                                                                                                                                                                     |
| Linienverlauf als Polyline (`journey_course`)              | ✅ ein Punkt pro Halt, Google-Encoded-Polyline                                                                                                                                                                                 |
| Verbindung wiederherstellen (`reconstruct`)                | ✅ über `Connection.ctx_recon`, holt frische Echtzeitdaten ohne neue Suche                                                                                                                                                     |
| Fahrplanperiode / Serverzeit (`server_info`)               | ✅                                                                                                                                                                                                                             |
| Aktuell betroffene Haltestellen (`affected_stops`)         | ✅ kurze Liste „wo klemmt es gerade", parameterlos                                                                                                                                                                              |
| Linien im Umkreis (`lines_in_area`)                        | ✅ vollständiger als `stop_details().lines` — enthält auch die Nachtlinien; Server deckelt bei 50                                                                                                                              |
| Störungen im Kartenausschnitt (`alerts_in_area`)           | ⚠️ funktioniert, war für Köln aber immer leer — nur Meldungen mit Geo-Bezug                                                                                                                                                    |
| Fußweg straßengenau (`walk_route`)                         | ✅ Polyline zu einem Fußweg-Abschnitt aus `trip_search` (`Leg.gis_ctx`); freies A-nach-B-Routing geht nicht, der Server akzeptiert nur selbst ausgegebene Tokens                                                                |
| Alternativen zu einer Verbindung (`trip_alternatives`)     | ✅ über `Connection.ctx_recon`, liefert spätere Verbindungen auf derselben Relation                                                                                                                                            |
| Push-Abos (`Subscr*`)                                      | ⚠️ Methoden existieren, brauchen aber ein Nutzerkonto — und sind die einzigen schreibenden, daher bewusst nicht angefasst                                                                                                      |
| Historische Daten jeder Art                                | ❌ kein Archiv, keine abgelaufenen Störungsmeldungen, keine Archiv-Methode — mit Messwerten belegt in [docs/API.md](docs/API.md#historische-daten)                                                                                                                |
| Auslastungsdaten                                           | ❌ nicht gefunden, vermutlich von KVB nicht befüllt                                                                                                                                                                            |
| Tarife/Preise                                              | ❌ keine einzige Preis-Methode vorhanden (alles `HAMM`)                                                                                                                                                                        |

**Vollständige API-Referenz mit allen Feldern, Beispiel-Requests und
Fehlercodes:** [docs/API.md](docs/API.md)

## Tests

```bash
uv run pytest tests/ -v
```

Tests laufen komplett gegen gemockte HTTP-Responses — keine Live-Calls gegen
den KVB-Server nötig.

## Nächste Schritte / Ideen

- Kartenansicht aus `vehicle_positions()` + `journey_course()` bauen — die
  `ani`-Tracks liefern die Zwischenschritte für flüssige Animation mit.
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

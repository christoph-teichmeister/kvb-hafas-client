# kvb-hafas-client

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)
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

```bash
pip install -r requirements.txt
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

Oder direkt über die CLI:

```bash
python example.py Neumarkt
```

## Was funktioniert

| Feature | Status |
|---|---|
| Haltestellensuche (`find_stops`) | ✅ |
| Umkreissuche (`nearby_stops`) | ✅ liefert aber auch POIs, kein reiner Stop-Filter |
| Echtzeit-Abfahrten (`station_board`) | ✅ inkl. Soll/Ist-Zeiten |
| Fahrtausfälle (`isCncl`-Flag) | ✅ vorhanden, noch nicht gegen echten Ausfall verifiziert |
| Zwischenhalte einer Fahrt (`journey_details`) | ✅ |
| Verbindungssuche (`trip_search`) | ✅ |
| Störungsmeldungen (`service_alerts`) | ✅ netzweit, inkl. Baustellen/Aufzugsausfälle — **kein Filter nach Haltestelle/Linie**, du musst client-seitig filtern (und die eingestreute KVB-Werbung, `category == 99`, selbst rausfiltern) |
| Historische Ist-Daten (Verspätungen/Ausfälle rückblickend) | ❌ nicht verfügbar, nur der Fahrplan der aktuellen Periode — siehe [docs/API.md](docs/API.md#historische-daten) |
| Auslastungsdaten | ❌ nicht gefunden, vermutlich von KVB nicht befüllt |

**Vollständige API-Referenz mit allen Feldern, Beispiel-Requests und
Fehlercodes:** [docs/API.md](docs/API.md)

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Tests laufen komplett gegen gemockte HTTP-Responses — keine Live-Calls gegen
den KVB-Server nötig.

## Nächste Schritte / Ideen

- `journey_details()`-Methode für Zwischenhalte (Rohdaten liegen in `stopL`
  jedes Journey-Objekts).
- `himL`/`msgL` (Störungsmeldungen) zusätzlich zum `isCncl`-Flag auswerten.
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

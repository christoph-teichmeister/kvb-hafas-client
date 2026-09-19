# Changelog

All notable changes to this project are documented in this file.


## [2026.9.4] - 2026-09-19

### Fixes
- preserve last commit line when building changelog entries (#10)


## [2026.9.3] - 2026-09-19

### Features

- redesign web UI and fix stale dashboard data (#9)

## [2026.9.2] - 2026-09-19

### Features

- bring the map/departures/dashboard HTTP server back into this repo (#8)

## [2026.9.1] - 2026-09-19

### Features

- automated CalVer release pipeline, changelog, and branch/commit conventions (#7)

## [2026.9.0] - 2026-09-18

### Features

- Initial KVB HAFAS client: `find_stops()`, `station_board()`, `journey_details()`, `nearby_stops()`, `service_alerts()` (HimSearch), `trip_search()` (TripSearch)
- Interactive CLI mit `questionary`/`rich`, Menüs für Departures, Trips, Alerts, Geo-Suche, Netzwerkinfos
- Live-Fahrzeugkarte (Web-UI) mit Echtzeitpositionen, Verspätungen, Linienfarben und detaillierter Zuggeometrie (OpenStreetMap für DB-Produkte)
- Soll-Fahrplan-Erfassung in SQLite für Taktungsanalysen (`timetable/`)
- API-Dokumentation mit Swagger UI und OpenAPI-Spec (`docs/API.md`, `docs/openapi.yaml`)
- Batching von Journey-Requests zur Reduktion der API-Calls
- CI-Workflow, der bei Push auf `main` das Companion-Repo `kvb-ha-map` benachrichtigt

### Fixes

- Haltestellen werden über Master-ID korrekt zusammengeführt, abgeschnittene Tafeln werden gemeldet
- Zeitdifferenzen in `analyze.sql` exakt statt über `julianday` berechnet

### Performance

- Kaltstart der Live-Karte beschleunigt

### Changed

- Umstellung des Paketmanagements auf `uv`
- Web-UI in das Package `kvb_hafas.webui` verschoben
- README überarbeitet (TOC, Badges, Contributing/License, vereinfachte Struktur)


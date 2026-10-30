# Lokale Server-API (`/api/*`)

Die Endpunkte, die `kvb_hafas/server/http_server.py` (`uv run map_server.py`, bzw. das
[kvb-ha-map](https://github.com/christoph-teichmeister/kvb-ha-map)-Add-on) für die Web-UI bereitstellt.
Das ist **nicht** die KVB-HAFAS-API — die steht in [API.md](API.md). Diese Endpunkte sind interne
Schnittstelle zwischen Server und `kvb_hafas/webui/`, kein stabiler Vertrag; maßgeblich ist der Code.

## Allgemeines

- Nur `GET`, Antworten als `application/json; charset=utf-8`. Unbekannte Pfade: `404 {"error": "not found"}`.
- **Fehler**: `400 {"error": "…"}` bei fehlenden/ungültigen Parametern (die Meldung wiederholt die Eingabe nicht),
  `502 {"error": "HAFAS request failed"}` wenn KVB einen Fehler meldet, `504 {"error": "KVB unreachable"}` bei
  Netzproblemen, `500 {"error": "internal error"}` sonst.
- **Zeitstempel**: Parameter `from`/`to` sind Unix-Sekunden (Bereich 0 bis 2100-01-01); Zeiten in Antworten sind je
  nach Feld Unix-Sekunden (`observed_at`, `buckets`) oder Unix-Millisekunden (`served_at`, `computed_at`, `eta`).
- **Anzahl-Parameter** (`max`) werden auf den erlaubten Bereich geklemmt statt abgelehnt; nicht-numerische Werte
  ergeben `400`.
- **Historie aus** (`HISTORY_ENABLED=false`): die `/api/stats/delays`, `/api/stats/punctuality-*` und
  `/api/history/*`-Endpunkte antworten `200` mit leerem Payload (bei den Stats-Endpunkten mit
  `"error": "history disabled"`).

## Konfiguration

| Pfad          | Parameter | Antwort                                                                                     |
|---------------|-----------|---------------------------------------------------------------------------------------------|
| `/api/config` | —         | Laufzeit-Konfiguration für die UI: `ingress_path`, Poll-/Cache-TTLs, `default_map_center`, `default_zoom`, `favorite_stop_ids`, `dashboard_refresh_seconds`, `history_enabled`, `track_seconds`, `late_from_minutes`, `tile_url`, `tile_attribution` |

## Live-Daten (Karte)

| Pfad            | Parameter                                                        | Antwort                                                                                                     |
|-----------------|------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| `/api/vehicles` | `bbox=minLat,minLon,maxLat,maxLon` (Pflicht, max. 1° je Achse)   | `{served_at, track_seconds, known_segments, prefetch: {pending, queued, current}, vehicles: [Vehicle…]}`; gecacht je gerundeter Bbox |
| `/api/alerts`   | —                                                                | `{alerts: [ServiceAlert…]}` (ohne Marketing-Meldungen)                                                      |
| `/api/network`  | —                                                                | `{lines: [{line, colour, category, paths: [[[lat, lon], …], …]}, …]}` — bekannte Linienverläufe              |
| `/api/stops`    | —                                                                | `{stops: [{id, name, lat, lon, lines, cats}, …]}` — Haltestellen aus den bekannten Verläufen                |

`Vehicle` und `ServiceAlert` sind die Dataclasses aus `kvb_hafas/models.py`, als JSON-Objekt serialisiert.

## Haltestellen und Abfahrten

| Pfad                 | Parameter                                                  | Antwort                                                                 |
|----------------------|------------------------------------------------------------|-------------------------------------------------------------------------|
| `/api/stops/search`  | `q` (Pflicht), `max` (Default 5, geklemmt auf 1–20)         | `{stops: [Stop…]}`                                                      |
| `/api/stops/details` | `ext_id` (Pflicht)                                          | `StopDetails` als Objekt                                                |
| `/api/departures`    | `stop_id` (Pflicht), `max` (Default 10, geklemmt auf 1–50)  | `{departures: [Departure… + delay_minutes]}` — `delay_minutes` ist `null` ohne (plausible) Echtzeit |
| `/api/journey/eta`   | `jid`, `lat`, `lon` (alle Pflicht)                          | `{jid, line, direction, computed_at, max_route_distance_m, nearest_stop, status, …}`; `status` ist `approaching`, `passed` oder `not_on_route`, die ersten beiden zusätzlich mit `eta`, `realtime`, `delay_minutes`, `cancelled`, `distance_m`, `point`, `between` |

## Statistiken

| Pfad                              | Parameter                                                     | Antwort                                                                                         |
|-----------------------------------|---------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `/api/stats/live`                 | `mode` (optional, filtert nach Verkehrsmittel)                | `{vehicle_count, vehicles_per_line, busiest_line, busiest_line_count, max_current_delay_minutes, max_current_delay_vehicle, delayed_vehicle_count, delayed_threshold_minutes, …}` aus dem letzten Fahrzeug-Snapshot |
| `/api/stats/delays`               | `from`, `to` (optional, Unix-s, auf volle Stunden abgerundet) | `{max_delay_minutes, max_delay_detail, lines: [{line, observations, avg_delay_minutes, punctuality_rate, …}], least_punctual_lines, late_from_minutes}` |
| `/api/stats/punctuality-trend`    | `window` = `7d` (Default) \| `24h` \| `1h`; Unbekanntes → `7d` | `{buckets: [unix_s…], lines: {line: [rate\|null…]}, counts: {line: [n…]}, late_from_minutes}`     |
| `/api/stats/punctuality-by-hour`  | `window` = `28d` (Default) \| `7d`; `days` = `all` (Default) \| `weekday` \| `sat` \| `sun` (sonst `400`) | `{hours: [0…23], days, lines: {line: [rate\|null ×24]}, counts: {line: [n ×24]}, late_from_minutes}` |

Die Statistik-Antworten werden serverseitig kurz gecacht.

## Fahrzeug-Historie

| Pfad                    | Parameter                                                                                                    | Antwort                                    |
|-------------------------|--------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `/api/history/trip`     | `jid` (Pflicht)                                                                                              | `{observations: [Observation…]}`, zeitlich aufsteigend |
| `/api/history/vehicles` | `line` (optional), `from`/`to` (optional, Unix-s). Ohne Angabe: die letzte Stunde; nur eine Grenze: eine Stunde ab bzw. bis dorthin. `to` vor `from` oder ein Fenster über 24 h → `400` | `{observations: [Observation…]}`, gedeckelt auf 5000 Zeilen |

`Observation` = eine Zeile der Tabelle `vehicle_observations`:
`{id, jid, line, category, direction, lat, lon, bearing, next_stop, delay_minutes, observed_at}`.

## Statische Dateien

Neben `/api/*` liefert der Server die UI aus `kvb_hafas/webui/`: `/`, `/index.html`, `/map.html`, `/departures.html`,
`/dashboard.html`, `/shared.css`, `/shared.js`, `/favicon.svg` sowie `/vendor/…` (Leaflet, lange gecacht).

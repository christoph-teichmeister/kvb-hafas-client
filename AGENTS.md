# AGENTS.md

Projektkontext und Konventionen für die Arbeit an `kvb-hafas-client`.

## Projekt

Inoffizieller Python-Client für Echtzeit-Fahrplandaten der Kölner Verkehrs-Betriebe (KVB), über den HAFAS `mgate`
-Endpoint (`https://auskunft.kvb.koeln/gate`). Keine offizielle API — nicht-kommerzielle Nutzung, rate-limit-freundlich,
keine KVB-Markenverwendung.

Companion-Repo: `kvb-ha-map` (Home Assistant Add-on), nutzt dieses Repo als ungepinnte Git-Dependency. Jedes Release
hier (`.github/workflows/release.yml`, letzter Schritt) löst einen `repository_dispatch` dort aus; `kvb-ha-map`
übernimmt die Release-Version 1:1 (Map-Version == Client-Version). `kvb-ha-map` besitzt
keinen eigenen Server-/UI-Code — es ist nur Docker/Ingress/Config-Verpackung um `kvb_hafas.server.http_server`, den es
direkt aus diesem Repo importiert und startet (`python -m kvb_hafas.server.http_server`). Server und UI werden also
ausschließlich hier gepflegt.

## Stack

- Python `>=3.9`, Build-Backend `hatchling`
- Dependency-/venv-Management über **uv**: `uv sync`, `uv run ...`
- Laufzeit-Dependency: `requests`
- Dev-Dependencies: `pytest`, `questionary`, `rich`, `ruff`
- Lint: `uv run ruff check .` (Regeln in `pyproject.toml` unter `[tool.ruff]`: `F`, `FA`, `B`, `UP`, Ziel `py39`).
  Neue Dateien mit `X | Y`-Annotationen brauchen `from __future__ import annotations` (Python 3.9). `ruff format`
  ist nicht eingeführt — kein Massen-Reformat.
- Versionierung: CalVer (`YYYY.M.PATCH`), wird von `.github/workflows/release.yml` automatisch bei Push auf `main`
  gesetzt — Version in `pyproject.toml` nicht von Hand ändern.

## Struktur

- `kvb_hafas/` — Kernbibliothek (`client.py`, `models.py`, `parsing.py`, `storage.py`)
- `kvb_hafas/webui/` — statische Web-UI-Assets als Package-Data (HTML, `shared.css`, `shared.js`, `vendor/`); neue
  Dateitypen müssen in `pyproject.toml` unter `artifacts` stehen
- `kvb_hafas/server/` — HTTP-Server für die Web-UI (`http_server.py` mit Handler/`main()`, `history_store.py`
  SQLite-Fahrzeug-Historie, `stats.py` Live-/historische Verspätungsstatistiken); wird auch von `kvb-ha-map` importiert
- `cli/` — interaktive Terminal-UI (`ui.py`, `format.py`, `departures.py`, `trips.py`, `alerts.py`, `geo.py`,
  `network.py`)
- `timetable/` — Fahrplan-Erfassung (`fetch.py`, `analyze.py`/`analyze.sql`)
- `tools/fetch_rail_geometry.py` — Gleisverläufe aus OSM nach `data/rail_geometry.json`; die Datei wird per
  `force-include` als `kvb_hafas/server/data/rail_geometry.json` ins Wheel gepackt
- `main.py` — CLI-Einstiegspunkt
- `map_server.py` — Einstiegspunkt des Web-UI-Servers (`uv run map_server.py`)
- `tests/` — pytest, HTTP vollständig gemockt
- `docs/API.md`, `docs/openapi.yaml` — Referenz der KVB-HAFAS-API; `docs/server-api.md` — lokale `/api/*`-Endpunkte
  des Servers (bei Änderungen an `http_server.py` mitpflegen)

## Tests

`uv run pytest tests/ -v` — keine Live-Calls gegen KVB, alles gemockt. Neue Features brauchen Tests gegen gemockte
HTTP-Responses.

CI: `.github/workflows/test.yml` läuft auf jedem PR und Push auf `main` mit Python 3.9 und 3.13 (`pytest` +
`ruff check`). `release.yml` führt die Tests vor dem Versions-Bump erneut aus. Actions sind auf Commit-SHAs gepinnt
(Tag als Kommentar), Dependabot (`.github/dependabot.yml`) hält sie aktuell.

## Commit- und PR-Konventionen (wichtig)

**Commits müssen dem Conventional-Commits-Format folgen:** `type(scope): subject`, z. B.
`fix: parse missing platform field`. Erlaubte Types:
`feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert`.

Grund: `.github/workflows/release.yml` parst Commit-Subjects nach diesen Prefixes, um den CalVer-Changelog zu bauen.
Falsches Format bricht die Changelog-Generierung.

**PR-Titel müssen ebenfalls Conventional-Commits-Format haben.** Wird von `.github/workflows/pr-title-lint.yml`
(`amannn/action-semantic-pull-request`) hart erzwungen — CI schlägt sonst fehl.

Die PR-Vorlage (`.github/PULL_REQUEST_TEMPLATE.md`) erinnert daran beim Erstellen eines PRs.

**Squash-Merge empfohlen:** Der PR-Titel wird dann zur einzigen Commit-Message auf `main` und landet so im
Changelog. Bei Merge-Commits zählen die einzelnen Commit-Subjects. `type!:` bzw. `type(scope)!:` erscheint im
Changelog unter „Breaking Changes“.

## Mitwirken

Vor größeren Änderungen erst ein Issue aufmachen. Lizenz: MIT.

# AGENTS.md

Projektkontext und Konventionen für die Arbeit an `kvb-hafas-client`.

## Projekt

Inoffizieller Python-Client für Echtzeit-Fahrplandaten der Kölner Verkehrs-Betriebe (KVB), über den HAFAS `mgate`
-Endpoint (`https://auskunft.kvb.koeln/gate`). Keine offizielle API — nicht-kommerzielle Nutzung, rate-limit-freundlich,
keine KVB-Markenverwendung.

Companion-Repo: `kvb-ha-map` (Home Assistant Add-on), nutzt dieses Repo als ungepinnte Git-Dependency. Push auf `main`
hier löst per `.github/workflows/notify-kvb-ha-map.yml` einen `repository_dispatch` dort aus. `kvb-ha-map` besitzt
keinen eigenen Server-/UI-Code — es ist nur Docker/Ingress/Config-Verpackung um `kvb_hafas.server.http_server`, den es
direkt aus diesem Repo importiert und startet (`python -m kvb_hafas.server.http_server`). Server und UI werden also
ausschließlich hier gepflegt.

## Stack

- Python `>=3.9`, Build-Backend `hatchling`
- Dependency-/venv-Management über **uv**: `uv sync`, `uv run ...`
- Laufzeit-Dependency: `requests`
- Dev-Dependencies: `pytest`, `questionary`, `rich`
- Versionierung: CalVer (`YYYY.M.PATCH`), wird von `.github/workflows/release.yml` automatisch bei Push auf `main`
  gesetzt — Version in `pyproject.toml` nicht von Hand ändern.

## Struktur

- `kvb_hafas/` — Kernbibliothek (`client.py`, `models.py`, `parsing.py`, `storage.py`)
- `kvb_hafas/webui/` — statische Web-UI-Assets als Package-Data
- `kvb_hafas/server/` — HTTP-Server für die Web-UI (`http_server.py` mit Handler/`main()`, `history_store.py`
  SQLite-Fahrzeug-Historie, `stats.py` Live-/historische Verspätungsstatistiken); wird auch von `kvb-ha-map` importiert
- `cli/` — interaktive Terminal-UI (`ui.py`, `format.py`, `departures.py`, `trips.py`, `alerts.py`, `geo.py`,
  `network.py`)
- `timetable/` — Fahrplan-Erfassung (`fetch.py`, `analyze.py`/`analyze.sql`)
- `main.py` — CLI-Einstiegspunkt
- `map_server.py` — Einstiegspunkt des Web-UI-Servers (`uv run map_server.py`)
- `tests/` — pytest, HTTP vollständig gemockt
- `docs/API.md`, `docs/openapi.yaml` — API-Referenz

## Tests

`uv run pytest tests/ -v` — keine Live-Calls gegen KVB, alles gemockt. Neue Features brauchen Tests gegen gemockte
HTTP-Responses.

## Commit- und PR-Konventionen (wichtig)

**Commits müssen dem Conventional-Commits-Format folgen:** `type(scope): subject`, z. B.
`fix: parse missing platform field`. Erlaubte Types:
`feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert`.

Grund: `.github/workflows/release.yml` parst Commit-Subjects nach diesen Prefixes, um den CalVer-Changelog zu bauen.
Falsches Format bricht die Changelog-Generierung.

**PR-Titel müssen ebenfalls Conventional-Commits-Format haben.** Wird von `.github/workflows/pr-title-lint.yml`
(`amannn/action-semantic-pull-request`) hart erzwungen — CI schlägt sonst fehl.

Die PR-Vorlage (`.github/PULL_REQUEST_TEMPLATE.md`) erinnert daran beim Erstellen eines PRs.

## Mitwirken

Vor größeren Änderungen erst ein Issue aufmachen. Lizenz: MIT.

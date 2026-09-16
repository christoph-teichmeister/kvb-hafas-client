#!/usr/bin/env python3
"""Soll-Fahrplan einer Linie für einen Betriebstag in eine SQLite-DB ziehen.

    uv run -m timetable.fetch --line 5 --date 2026-09-17

Zweistufig:
  1. Discovery — Abfahrts- und Ankunftstafel an einer *ruhigen* Endhaltestelle.
     DEP und ARR decken zusammen beide Fahrtrichtungen ab; an einem Halt mit
     wenig Verkehr passt der ganze Betriebstag in je einen Request.
  2. Expansion — pro gefundener Fahrt einmal JourneyDetails, das liefert den
     kompletten Laufweg mit Soll-Zeiten je Halt.

Nur Soll-Zeiten: für vergangene Tage liefert HAFAS keine Ist-Werte, und für
zukünftige gibt es sie naturgemäß nicht (siehe docs/API.md, "Historische Daten").
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import requests

from kvb_hafas import KVBHafasClient, KVBHafasError, storage

# Ruhige Endhaltestellen, an denen ein Board den ganzen Tag abdeckt.
# Heumarkt taugt dafür nicht: dort verdrängen die anderen Linien die Liste,
# Linie 5 bricht dann mitten am Nachmittag ab.
ANCHORS = {
    "5": "900000903",  # Köln Ossendorf Sparkasse am Butzweilerhof
}

BOARD_MAX = 1000  # serverseitig durch die Dichte des Halts gedeckelt
CHUNK = 10  # Fahrten pro POST; das Gate nimmt mehrere svcReqL-Einträge an
MAX_PLAUSIBLE_GAP_MIN = 60  # größere Lücke im Tagesverkehr = abgeschnittene Tafel


def _coverage_gap(departures: list, service_date) -> tuple[str, str, int] | None:
    """Größte Lücke zwischen zwei aufeinanderfolgenden Fahrten, falls verdächtig.

    Die nächtliche Betriebspause taucht hier nicht auf: nach Zeitstempel
    sortiert liegt sie außerhalb von erster und letzter Fahrt, nicht
    dazwischen. Eine Lücke *innerhalb* des Betriebstags heißt dagegen, dass
    die Tafel abgeschnitten wurde.
    """
    stamps = sorted(t for t in (storage.parse_hafas_time(d.planned, service_date) for d in departures) if t)
    worst = None
    for earlier, later in zip(stamps, stamps[1:]):
        minutes = int((datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() // 60)
        if minutes > MAX_PLAUSIBLE_GAP_MIN and (worst is None or minutes > worst[2]):
            worst = (earlier, later, minutes)
    return worst


def discover(client: KVBHafasClient, line: str, anchor: str, service_date) -> dict[str, str]:
    """jid -> Richtung, für beide Fahrtrichtungen.

    Ein Board deckt nur so viele Fahrten ab, wie der Halt hergibt: an einem
    dichten Knoten bricht die Liste mitten am Tag ab. Weil die Fahrten, die
    dann fehlen, nirgends angemeldet werden, wird die Abdeckung hier geprüft
    statt sie zu unterstellen — sonst fehlt am Ende ein Teil des Fahrplans,
    ohne dass es irgendwo auffällt.
    """
    date_hafas = service_date.strftime("%Y%m%d")
    found: dict[str, str] = {}
    for board_type in ("DEP", "ARR"):
        try:
            board = client.station_board(
                anchor, max_journeys=BOARD_MAX, date=date_hafas, time="000000", board_type=board_type
            )
        except (KVBHafasError, requests.RequestException) as exc:
            print(f"  !! {board_type}-Tafel fehlgeschlagen: {exc}", file=sys.stderr)
            continue

        hits = [d for d in board if d.line == line and d.jid]
        capped = len(board) >= BOARD_MAX
        gap = _coverage_gap(hits, service_date)
        print(f"  {board_type}: {len(hits)} Fahrten der Linie {line} (von {len(board)} gesamt)")

        if gap:
            # Die Tafel ist nicht durchgehend nach Zeit sortiert: sie läuft am
            # Ende auf den Tagesanfang über. Erste und letzte Zeit sehen dadurch
            # vollständig aus, auch wenn mittendrin Stunden fehlen — deshalb wird
            # hier auf die Lücke geprüft und nicht auf die Spanne.
            print(
                f"  !! Zwischen {gap[0][11:16]} und {gap[1][11:16]} fehlen {gap[2]} Minuten "
                f"aus der {board_type}-Tafel.\n"
                f"     Haltestelle {anchor} ist zu dicht befahren"
                f"{' (Limit von ' + str(BOARD_MAX) + ' Fahrten erreicht)' if capped else ''} — "
                f"die anderen Linien\n"
                f"     verdrängen die Liste. Eine ruhigere Endhaltestelle als --anchor wählen,\n"
                f"     sonst fehlt dieser Teil des Fahrplans in der DB.",
                file=sys.stderr,
            )
        for dep in hits:
            found.setdefault(dep.jid, dep.direction)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line", default="5", help="Linien-Label, z.B. 5")
    ap.add_argument("--date", required=True, help="Betriebstag als YYYY-MM-DD")
    ap.add_argument("--db", default="timetable.db")
    ap.add_argument("--anchor", help="extId der Endhaltestelle (überschreibt die Voreinstellung)")
    ap.add_argument("--interval", type=float, default=1.0, help="Mindestabstand zwischen Requests in Sekunden")
    args = ap.parse_args()

    service_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    date_hafas = service_date.strftime("%Y%m%d")
    anchor = args.anchor or ANCHORS.get(args.line)
    if not anchor:
        print(f"Keine Anker-Haltestelle für Linie {args.line} hinterlegt — bitte --anchor angeben.", file=sys.stderr)
        return 2

    client = KVBHafasClient(min_interval=args.interval)

    info = client.server_info()
    if not (info.timetable_from <= date_hafas <= info.timetable_to):
        print(
            f"{args.date} liegt außerhalb der Fahrplanperiode "
            f"({info.timetable_from}-{info.timetable_to}) — HAFAS würde H9360 liefern.",
            file=sys.stderr,
        )
        return 2

    conn = storage.connect(args.db)

    print(f"Discovery an {anchor} für {args.date}:")
    jids = discover(client, args.line, anchor, service_date)
    if not jids:
        print("Keine Fahrten gefunden — Anker oder Datum prüfen.", file=sys.stderr)
        return 1

    done = storage.known_jids(conn)
    todo = [j for j in jids if j not in done]
    print(f"\n{len(jids)} Fahrten gefunden, {len(jids) - len(todo)} bereits in der DB, {len(todo)} zu holen.")

    failed = 0
    done_cnt = 0
    # Gebündelt: ein POST pro CHUNK Fahrten statt pro Fahrt — das spart vor allem
    # die min_interval-Pause, die sonst jede einzelne Fahrt kostet.
    for start in range(0, len(todo), CHUNK):
        batch = todo[start : start + CHUNK]
        try:
            routes = client.journey_routes(batch, chunk=CHUNK)
        except (KVBHafasError, requests.RequestException) as exc:
            # Ein einzelner Aussetzer darf den Lauf nicht kosten — beim nächsten
            # Aufruf werden die Fahrten erneut versucht, weil sie nicht in der DB stehen.
            failed += len(batch)
            print(f"  !! {batch[0]}…{batch[-1]}: {exc}", file=sys.stderr)
            continue
        failed += len(batch) - len(routes)  # Teil-Antworten != OK fehlen im Ergebnis
        for route in routes:
            storage.store_route(conn, route)
        conn.commit()
        done_cnt += len(batch)
        print(f"  {done_cnt}/{len(todo)} Laufwege geholt")

    conn.commit()
    c = storage.counts(conn)
    print(f"\nDB {args.db}: {c['journeys']} Fahrten, {c['stops']} Halte, {c['stop_times']} Fahrplanzeiten")
    if failed:
        print(f"{failed} Fahrten fehlgeschlagen — Skript erneut aufrufen, um sie nachzuholen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

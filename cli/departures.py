"""Abfahrtstafel einer Haltestelle."""

from __future__ import annotations

from itertools import groupby

from rich import box
from rich.markup import escape as esc
from rich.table import Table

from kvb_hafas import Departure, KVBHafasClient
from kvb_hafas.parsing import _delay_minutes, _hafas_minutes

from cli.format import hhmm
from cli.ui import ask, console, panel, pick_stop


def _planned_minutes(dep: Departure) -> int:
    """Soll-Abfahrt in Minuten ab Mitternacht des Abfragetags.

    HAFAS hängt Abfahrten nach Mitternacht ein Tages-Prefix an ("01000500"),
    deshalb sortiert 00:05 so hinter 23:50 statt davor. Unlesbare Zeiten
    landen am Ende.
    """
    try:
        return _hafas_minutes(dep.planned)
    except (ValueError, TypeError):
        return 1 << 30


def live_cell(dep: Departure) -> str:
    """Echtzeit-Spalte: Ausfall, neue Zeit mit Verspätung oder leer (pünktlich/ohne Echtzeit)."""
    if dep.cancelled:
        return "[red bold]AUSFALL[/]"
    if not dep.realtime or dep.realtime == dep.planned:
        return ""
    mins = _delay_minutes(dep.planned, dep.realtime)
    if mins is None:
        # Zeit nicht auswertbar oder unplausibel: Echtzeit zeigen, aber keine erfundene "(+0)".
        return f"[yellow]{hhmm(dep.realtime)}[/]"
    if mins == 0:
        return ""
    return f"[{'red' if mins > 3 else 'yellow'}]{hhmm(dep.realtime)} ({mins:+d})[/]"


def departure_table(deps: list[Departure]) -> Table:
    """Abfahrtstafel als Tabelle, nach Gleis/Steig gruppiert."""
    table = Table(box=box.SIMPLE, header_style="dim")
    table.add_column("Gleis", style="bold")
    table.add_column("Ab", style="bold", justify="right")
    table.add_column("Linie", style="cyan bold", justify="right")
    table.add_column("Richtung")
    table.add_column("Echtzeit")

    deps.sort(key=lambda d: (d.platform or "", _planned_minutes(d)))
    for platform, group in groupby(deps, key=lambda d: d.platform):
        # Fallback-Werte kommen schon als "Steig N", echte dPlatf-Werte als "2"/"A".
        label = platform if (platform or "").startswith("Steig") else f"Gleis {platform or '?'}"
        table.add_section()
        for i, dep in enumerate(group):
            table.add_row(label if i == 0 else "", hhmm(dep.planned), dep.line, esc(dep.direction), live_cell(dep))
    return table


def show_departures(client: KVBHafasClient) -> None:
    stop = pick_stop(client)
    if not stop:
        return
    count = ask("Anzahl Abfahrten", "10")
    with console.status("[cyan]Lade Abfahrten…"):
        deps = client.station_board(stop.ext_id, max_journeys=int(count) if count.isdigit() else 10)
    if not deps:
        console.print("[dim]Keine Abfahrten.[/]")
        return
    console.print(panel(f"Abfahrten {esc(stop.name)}", departure_table(deps)))

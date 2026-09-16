"""Abfahrtstafel einer Haltestelle."""

from itertools import groupby

from rich import box
from rich.markup import escape as esc
from rich.table import Table

from kvb_hafas import Departure, KVBHafasClient

from cli.format import delay_min, hhmm
from cli.ui import ask, console, panel, pick_stop

def departure_table(deps: list[Departure]) -> Table:
    """Abfahrtstafel als Tabelle, nach Gleis/Steig gruppiert."""
    table = Table(box=box.SIMPLE, header_style="dim")
    table.add_column("Gleis", style="bold")
    table.add_column("Ab", style="bold", justify="right")
    table.add_column("Linie", style="cyan bold", justify="right")
    table.add_column("Richtung")
    table.add_column("Echtzeit")

    deps.sort(key=lambda d: (d.platform or "", d.planned))
    for platform, group in groupby(deps, key=lambda d: d.platform):
        # Fallback-Werte kommen schon als "Steig N", echte dPlatf-Werte als "2"/"A".
        label = platform if (platform or "").startswith("Steig") else f"Gleis {platform or '?'}"
        table.add_section()
        for i, dep in enumerate(group):
            live = ""
            if dep.cancelled:
                live = "[red bold]AUSFALL[/]"
            elif dep.realtime and dep.realtime != dep.planned:
                mins = delay_min(dep.planned, dep.realtime)
                live = f"[{'red' if mins > 3 else 'yellow'}]{hhmm(dep.realtime)} ({mins:+d})[/]"
            table.add_row(label if i == 0 else "", hhmm(dep.planned), dep.line, esc(dep.direction), live)
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

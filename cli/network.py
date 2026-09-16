"""Netzweite Stammdaten: Linien und Serverinfo."""

from rich import box
from rich.console import Group
from rich.markup import escape as esc
from rich.table import Table

from kvb_hafas import KVBHafasClient

from cli.format import ddmm, hhmm
from cli.ui import ask, choose, console, panel

def show_line(client: KVBHafasClient) -> None:
    query = ask("Linie suchen (z.B. 18, 146)")
    if not query:
        return
    with console.status("[cyan]Suche Linien…"):
        lines = client.find_lines(query)
    if not lines:
        console.print("[dim]Keine Linie gefunden.[/]")
        return
    idx = choose("Treffer", [f"{ln.name}  ({ln.line_id})" for ln in lines])
    if idx is None:
        return
    with console.status("[cyan]Lade Liniendetails…"):
        line = client.line_details(lines[idx].line_id)
        journeys = client.find_journeys(line.name)
    head = Table(box=box.SIMPLE, show_header=False)
    head.add_column("", style="dim")
    head.add_column("")
    head.add_row("Linie", f"[cyan bold]{esc(line.name)}[/]  [dim]{esc(line.line_id)}[/]")
    head.add_row("Art", esc(line.category or "—"))
    head.add_row("Betreiber", esc(line.operator or "—"))
    head.add_row("Fahrten im Fahrplan", str(line.journeys if line.journeys is not None else "—"))
    parts: list[object] = [head]
    if journeys:
        table = Table(box=box.SIMPLE, header_style="dim")
        table.add_column("Ab", justify="right")
        table.add_column("An", justify="right")
        table.add_column("Von → Nach")
        table.add_column("Verkehrstage", style="dim")
        for jny in journeys[:10]:
            table.add_row(
                hhmm(jny.dep_time),
                hhmm(jny.arr_time),
                f"{esc(jny.from_name)} → {esc(jny.to_name)}",
                esc(jny.service_days),
            )
        parts.append(table)
    console.print(panel(f"Linie {esc(line.name)}", Group(*parts)))


def show_server_info(client: KVBHafasClient) -> None:
    with console.status("[cyan]Frage Server…"):
        info = client.server_info()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("", style="dim")
    table.add_column("")
    table.add_row("Fahrplanperiode", f"{ddmm(info.timetable_from)}{info.timetable_from[:4]} – {ddmm(info.timetable_to)}{info.timetable_to[:4]}")
    table.add_row("Serverzeit", f"{ddmm(info.date)}{info.date[:4]} {hhmm(info.time)}")
    console.print(panel("Server", table))

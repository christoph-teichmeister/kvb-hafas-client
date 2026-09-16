"""Störungsmeldungen — netzweit, je Haltestelle oder je Linie."""

import questionary
from rich import box
from rich.markup import escape as esc
from rich.table import Table

from kvb_hafas import KVBHafasClient, ServiceAlert

from cli.format import ddmm
from cli.ui import STYLE, ask, choose, console, panel, pick_stop, prompt

def alert_table(alerts: list[ServiceAlert]) -> Table | str:
    """Meldungen als zweispaltige Tabelle; leere Liste -> Hinweistext."""
    if not alerts:
        return "[dim]keine[/]"
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Zeitraum", style="dim", no_wrap=True)
    table.add_column("Meldung")
    for alert in alerts:
        table.add_row(f"{ddmm(alert.valid_from)}–{ddmm(alert.valid_to)}", esc(alert.text))
    return table


def show_alerts(client: KVBHafasClient) -> None:
    idx = choose("Störungsmeldungen filtern nach", ["Haltestelle", "Linie", "netzweit (alle)"])
    if idx is None:
        return
    stop = pick_stop(client) if idx == 0 else None
    if idx == 0 and stop is None:
        return
    line = ask("Linie (z.B. 18, 133)") if idx == 1 else None
    if idx == 1 and not line:
        return

    hide_ads = prompt(questionary.confirm("Werbung (cat 99) ausblenden?", default=True, style=STYLE, qmark="›"))
    with console.status("[cyan]Lade Meldungen…"):
        alerts = client.service_alerts(stop, line=line)
    if hide_ads:
        alerts = [a for a in alerts if a.category != 99]
    scope = stop.name if stop else (f"Linie {line}" if line else "gesamtes Netz")
    console.print(panel(f"Meldungen für {esc(scope)}", alert_table(alerts[:10])))

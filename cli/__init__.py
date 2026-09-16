"""Interaktive Terminal-Oberfläche für den KVB-HAFAS-Client."""

from rich.markup import escape as esc

from kvb_hafas import KVBHafasClient

from cli.alerts import show_alerts
from cli.departures import show_departures
from cli.geo import show_nearby, show_reachable, show_vehicles
from cli.network import show_line, show_server_info
from cli.trips import show_trip
from cli.ui import choose, console

__all__ = ["main"]

MENU: list[tuple[str, object]] = [
    ("Abfahrten einer Haltestelle", show_departures),
    ("Verbindung suchen", show_trip),
    ("Haltestellen in der Nähe", show_nearby),
    ("Störungsmeldungen", show_alerts),
    ("Erreichbar in X Minuten", show_reachable),
    ("Fahrzeuge live in der Nähe", show_vehicles),
    ("Linie nachschlagen", show_line),
    ("Serverinfo / Fahrplanperiode", show_server_info),
    ("Beenden", None),
]


def main() -> None:
    client = KVBHafasClient()
    console.print("[bold]KVB HAFAS Client[/] [dim]— interaktiv[/]")
    while True:
        idx = choose("Was möchtest du tun?", [label for label, _ in MENU])
        action = MENU[idx][1] if idx is not None else None
        if action is None:
            console.print("[dim]Tschüss 👋")
            return
        try:
            action(client)
        except Exception as exc:  # Netzfehler/HAFAS-Fehler nicht das Menü killen lassen
            console.print(f"[red]Fehler:[/] {esc(str(exc))}")

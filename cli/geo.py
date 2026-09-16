"""Ortsbezogene Abfragen: Haltestellen, Einzugsgebiet, Fahrzeugpositionen."""

from rich import box
from rich.markup import escape as esc
from rich.table import Table

from kvb_hafas import KVBHafasClient

from cli.ui import ask, console, panel, pick_stop

def show_nearby(client: KVBHafasClient) -> None:
    try:
        lat = float(ask("Breitengrad (lat)", "50.9375"))
        lon = float(ask("Längengrad (lon)", "6.9603"))
        dist = int(ask("Umkreis in Metern", "500"))
    except ValueError:
        console.print("[red]Ungültige Koordinaten.[/]")
        return
    with console.status("[cyan]Suche Haltestellen…"):
        stops = client.nearby_stops(lat, lon, max_dist_m=dist, with_lines=True)
    title = f"In {dist} m Umkreis"
    if not stops:
        console.print(panel(title, "[dim]nichts gefunden[/]"))
        return
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Haltestelle")
    table.add_column("Linien", style="cyan")
    table.add_column("ID", style="dim")
    for stop in stops:
        table.add_row(esc(stop.name), esc(" · ".join(stop.lines)) or "[dim]—[/]", stop.ext_id)
    console.print(panel(title, table))


def show_reachable(client: KVBHafasClient) -> None:
    stop = pick_stop(client)
    if not stop:
        return
    minutes = ask("Maximale Fahrzeit in Minuten", "15")
    changes = ask("Maximale Umstiege", "0")
    with console.status("[cyan]Berechne Einzugsgebiet…"):
        reachable = client.reachable_stops(
            stop.ext_id,
            max_minutes=int(minutes) if minutes.isdigit() else 15,
            max_changes=int(changes) if changes.isdigit() else 0,
        )
    title = f"Ab {esc(stop.name)} in {minutes} min erreichbar"
    if not reachable:
        console.print(panel(title, "[dim]nichts gefunden[/]"))
        return
    table = Table(box=box.SIMPLE, header_style="dim")
    table.add_column("Min", justify="right", style="bold")
    table.add_column("Ums.", justify="right", style="dim")
    table.add_column("Haltestelle")
    for entry in reachable:
        table.add_row(str(entry.minutes), str(entry.changes), esc(entry.stop.name))
    console.print(panel(f"{title} ({len(reachable)})", table))


def show_vehicles(client: KVBHafasClient) -> None:
    try:
        lat = float(ask("Breitengrad (lat)", "50.9375"))
        lon = float(ask("Längengrad (lon)", "6.9603"))
        radius_km = float(ask("Radius in km", "2"))
    except ValueError:
        console.print("[red]Ungültige Eingabe.[/]")
        return
    # Grobe Grad-Umrechnung; reicht für eine Bounding-Box auf Kölner Breite.
    d_lat = radius_km / 111.0
    d_lon = radius_km / 71.0
    with console.status("[cyan]Lade Fahrzeugpositionen…"):
        vehicles = client.vehicle_positions(lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon)
    title = f"Fahrzeuge im Umkreis von {radius_km} km"
    if not vehicles:
        console.print(panel(title, "[dim]keine unterwegs[/]"))
        return
    table = Table(box=box.SIMPLE, header_style="dim")
    table.add_column("Linie", style="cyan bold", justify="right")
    table.add_column("Richtung")
    table.add_column("Position", style="dim")
    for vehicle in vehicles:
        table.add_row(esc(vehicle.line), esc(vehicle.direction), f"{vehicle.lat:.5f}, {vehicle.lon:.5f}")
    console.print(panel(f"{title} ({len(vehicles)})", table))

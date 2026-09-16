"""Verbindungssuche, Verbindungsdetails und Fußwege."""

from dataclasses import replace
from datetime import datetime

from rich import box
from rich.console import Group
from rich.markup import escape as esc
from rich.table import Table
from rich.tree import Tree

from kvb_hafas import Connection, KVBHafasClient, Leg

from cli.format import dur_min, hhmm
from cli.ui import ask, choose, console, panel, pick_stop

def merge_walks(legs: list[Leg]) -> list[Leg]:
    """Aufeinanderfolgende Fußwege zu einem zusammenfassen.

    HAFAS zerlegt den Weg zwischen zwei Steigen derselben Haltestelle gern in
    vier 1-35-m-Häppchen — als einzelne Zeilen ist das nur Rauschen.
    """
    out: list[Leg] = []
    for leg in legs:
        if leg.walk and out and out[-1].walk:
            prev = out[-1]
            out[-1] = replace(
                prev,
                to_name=leg.to_name,
                arr_time=leg.arr_time,
                dist_m=(prev.dist_m or 0) + (leg.dist_m or 0),
            )
        else:
            out.append(leg)
    return out


def connection_tree(con: Connection) -> Tree:
    """Eine Verbindung als Baum: Fahrten mit Ein-/Ausstieg, dazwischen Fußwege."""
    changes = f"{con.num_changes} Umstieg{'e' if con.num_changes != 1 else ''}"
    tree = Tree(f"[bold]{hhmm(con.dep_time)} → {hhmm(con.arr_time)}[/]  [dim]· {changes}[/]", guide_style="dim")
    for leg in merge_walks(con.legs):
        if leg.walk:
            dist = f", {leg.dist_m} m" if leg.dist_m else ""
            tree.add(f"[dim]Fußweg {hhmm(leg.dep_time)}–{hhmm(leg.arr_time)} ({esc(leg.to_name)}{dist})[/]")
            continue
        node = tree.add(f"[cyan bold]{esc(leg.line or '?')}[/] → {esc(leg.direction or '')}")
        for verb, color, time, name, platf in (
            ("ab", "green", leg.dep_time, leg.from_name, leg.dep_platform),
            ("an", "red", leg.arr_time, leg.to_name, leg.arr_platform),
        ):
            gleis = f"  [dim]· Gleis {platf}[/]" if platf else ""
            node.add(f"[{color}]{verb}[/] {hhmm(time)}  {esc(name)}{gleis}")
    return tree


def dedupe_connections(cons: list[Connection]) -> list[Connection]:
    """Identische Verbindungen rauswerfen — SearchOnTrip liefert Dubletten.

    Signatur über alle Abschnitte, nicht nur Ab/An: zwei Verbindungen mit
    gleichen Eckzeiten können echt verschiedene Wege sein.
    """
    seen, out = set(), []
    for con in cons:
        key = (con.dep_time, con.arr_time, tuple((leg.line, leg.dep_time, leg.from_name) for leg in con.legs))
        if key not in seen:
            seen.add(key)
            out.append(con)
    return out


def show_walk_routes(client: KVBHafasClient, con: Connection) -> None:
    """Zu jedem Fußweg der Verbindung die straßengenaue Geometrie holen."""
    # Ungemergte Legs: merge_walks() fasst Fußwege zusammen und behält dabei
    # nur den gis_ctx des ersten — hier braucht jeder Abschnitt seinen eigenen.
    walks = [leg for leg in con.legs if leg.walk and leg.gis_ctx and leg.dist_m]
    if not walks:
        console.print("[dim]Kein Fußweg mit Geometrie in dieser Verbindung.[/]")
        return
    table = Table(box=box.SIMPLE, header_style="dim")
    table.add_column("Abschnitt")
    table.add_column("Meter", justify="right")
    table.add_column("Dauer", justify="right")
    table.add_column("Stützpunkte", justify="right")
    table.add_column("Start → Ziel", style="dim")
    with console.status("[cyan]Lade Fußwege…"):
        for leg in walks:
            route = client.walk_route(leg.gis_ctx)
            ends = ""
            if route.points:
                first, last = route.points[0], route.points[-1]
                ends = f"{first[0]:.5f},{first[1]:.5f} → {last[0]:.5f},{last[1]:.5f}"
            table.add_row(
                f"{esc(leg.from_name)} → {esc(leg.to_name)}",
                str(route.dist_m),
                dur_min(route.duration),
                str(len(route.points)),
                ends,
            )
    console.print(panel("Fußwege straßengenau", table))


def show_connection_detail(client: KVBHafasClient, con: Connection) -> None:
    """Folgeabfragen zu einer ausgewählten Verbindung."""
    while True:
        idx = choose(
            f"{hhmm(con.dep_time)} → {hhmm(con.arr_time)}",
            ["Spätere Alternativen", "Fußwege straßengenau", "Echtzeit aktualisieren", "zurück"],
        )
        if idx is None or idx == 3:
            return
        if idx == 0:
            with console.status("[cyan]Suche Alternativen…"):
                alts = client.trip_alternatives(con.ctx_recon)
            alts = dedupe_connections(alts)
            if not alts:
                console.print("[dim]Keine Alternativen.[/]")
                continue
            console.print(panel(f"Alternativen ({len(alts)})", Group(*(connection_tree(a) for a in alts[:8]))))
        elif idx == 1:
            show_walk_routes(client, con)
        else:
            with console.status("[cyan]Hole frische Echtzeitdaten…"):
                fresh = client.reconstruct(con.ctx_recon)
            if not fresh:
                console.print("[dim]Verbindung nicht mehr auflösbar.[/]")
                continue
            con = fresh[0]
            console.print(panel("Aktualisiert", connection_tree(con)))


def show_trip(client: KVBHafasClient) -> None:
    start = pick_stop(client, "Start")
    if not start:
        return
    dest = pick_stop(client, "Ziel")
    if not dest:
        return
    now = datetime.now()
    date = ask("Datum (YYYYMMDD)", now.strftime("%Y%m%d"))
    time = ask("Uhrzeit (HHMM)", now.strftime("%H%M"))
    with console.status("[cyan]Suche Verbindungen…"):
        cons = client.trip_search(start.ext_id, dest.ext_id, date=date, time=f"{time[:4]}00")
    title = f"{esc(start.name)} → {esc(dest.name)}"
    if not cons:
        console.print(panel(title, "[dim]keine Verbindung gefunden[/]"))
        return
    console.print(panel(title, Group(*(connection_tree(con) for con in cons))))

    while True:
        labels = [f"{hhmm(c.dep_time)} → {hhmm(c.arr_time)}  ({c.num_changes} Ums.)" for c in cons]
        idx = choose("Verbindung im Detail", [*labels, "zurück"])
        if idx is None or idx == len(cons):
            return
        show_connection_detail(client, cons[idx])

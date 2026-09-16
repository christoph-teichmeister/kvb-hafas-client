"""Interaktiver CLI-Entry-Point: durch Menüs zur gewünschten Abfrage."""

from dataclasses import replace
from datetime import datetime
from itertools import groupby

import questionary
from rich import box
from rich.console import Console, Group
from rich.markup import escape as esc
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from kvb_hafas import Connection, Departure, KVBHafasClient, Leg, ServiceAlert, Stop

console = Console()

# Pfeiltasten-Auswahl im gleichen Cyan wie die rich-Ausgabe.
STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:cyan"),
        ("answer", "fg:cyan bold"),
        ("instruction", "fg:#888888"),
    ]
)


def hhmm(t: str) -> str:
    """HAFAS-Zeit ("224400", ggf. mit Tages-Prefix "01224400") -> "22:44"."""
    t = t[-6:]
    return f"{t[:2]}:{t[2:4]}" if len(t) == 6 else t


def ddmm(d: str) -> str:
    """HAFAS-Datum ("20260916") -> "16.09."; alles andere unverändert."""
    return f"{d[6:8]}.{d[4:6]}." if len(d) == 8 and d.isdigit() else d


def dur_min(d: str) -> str:
    """HAFAS-Dauer ("000200") -> "2 min"; leer -> "—"."""
    if len(d) != 6 or not d.isdigit():
        return "—"
    return f"{int(d[:2]) * 60 + int(d[2:4])} min"


def delay_min(planned: str, realtime: str) -> int:
    """Verspätung in Minuten aus zwei HAFAS-Zeiten (ohne Tagesüberlauf)."""

    def minutes(t: str) -> int:
        t = t[-6:]
        return int(t[:2]) * 60 + int(t[2:4])

    return minutes(realtime) - minutes(planned)


def panel(title: str, content: object) -> Panel:
    """Ergebnis in einen Rahmen mit Titel setzen."""
    return Panel(content, title=f"[bold]{title}[/]", title_align="left", border_style="cyan", padding=(0, 1))


def prompt(question: questionary.Question) -> object | None:
    """Frage stellen; None bei Ctrl-C oder wenn kein Terminal dranhängt.

    Ohne TTY (Pipe, Redirect) wirft prompt_toolkit EOFError — das soll ein
    sauberer Abbruch sein, kein Traceback.
    """
    try:
        return question.ask()
    except EOFError:
        return None


def ask(text: str, default: str = "") -> str:
    """Texteingabe mit Default. Ctrl-C/Ctrl-D beenden das Programm."""
    answer = prompt(questionary.text(text, default=default, style=STYLE, qmark="›"))
    if answer is None:
        raise SystemExit("Abbruch.")
    return str(answer).strip()


def choose(title: str, options: list[str]) -> int | None:
    """Auswahl per Pfeiltasten oder Zifferntaste. None = abgebrochen."""
    question = questionary.select(
        title,
        choices=[questionary.Choice(opt, value=i) for i, opt in enumerate(options)],
        style=STYLE,
        qmark="›",
        instruction="(↑/↓ oder Ziffer, Enter)",
        # use_shortcuts belegt 1-9; bei mehr Optionen gäbe es doppelte Tasten.
        use_shortcuts=len(options) <= 9,
    )
    answer = prompt(question)
    return answer if isinstance(answer, int) else None


def pick_stop(client: KVBHafasClient, label: str = "Haltestelle") -> Stop | None:
    """Nach einer Haltestelle suchen und aus den Treffern auswählen."""
    while True:
        query = ask(f"{label} suchen (leer = zurück)")
        if not query:
            return None
        with console.status(f"[cyan]Suche „{esc(query)}“…"):
            stops = client.find_stops(query)
        if not stops:
            console.print(f"[dim]Keine Haltestelle für „{esc(query)}“ gefunden.[/]")
            continue
        idx = choose("Treffer", [f"{s.name}  ({s.ext_id})" for s in stops])
        if idx is not None:
            return stops[idx]


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


if __name__ == "__main__":
    main()

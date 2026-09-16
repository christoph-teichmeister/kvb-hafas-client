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
        stops = client.nearby_stops(lat, lon, max_dist_m=dist)
    title = f"In {dist} m Umkreis"
    if not stops:
        console.print(panel(title, "[dim]nichts gefunden[/]"))
        return
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Haltestelle")
    table.add_column("ID", style="dim")
    for stop in stops:
        table.add_row(esc(stop.name), stop.ext_id)
    console.print(panel(title, table))


MENU: list[tuple[str, object]] = [
    ("Abfahrten einer Haltestelle", show_departures),
    ("Verbindung suchen", show_trip),
    ("Haltestellen in der Nähe", show_nearby),
    ("Störungsmeldungen", show_alerts),
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

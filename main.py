"""Interaktiver CLI-Entry-Point: durch Menüs zur gewünschten Abfrage."""

from dataclasses import replace
from datetime import datetime
from itertools import groupby

from kvb_hafas import KVBHafasClient, Leg, Stop


def hhmm(t: str) -> str:
    """HAFAS-Zeit ("224400", ggf. mit Tages-Prefix "01224400") -> "22:44"."""
    t = t[-6:]
    return f"{t[:2]}:{t[2:4]}" if len(t) == 6 else t


def ask(prompt: str, default: str = "") -> str:
    """Eingabe mit Default; Ctrl-D/Ctrl-C beenden das Programm."""
    suffix = f" [{default}]" if default else ""
    try:
        return input(f"{prompt}{suffix}: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nAbbruch.")


def choose(title: str, options: list[str]) -> int | None:
    """Nummerierte Auswahl. Gibt den Index zurück, None bei leerer Eingabe."""
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        raw = ask("Auswahl", "1" if options else "")
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("Ungültige Eingabe.")


def pick_stop(client: KVBHafasClient, label: str = "Haltestelle") -> Stop | None:
    """Nach einer Haltestelle suchen und aus den Treffern auswählen."""
    while True:
        query = ask(f"{label} suchen (leer = zurück)")
        if not query:
            return None
        stops = client.find_stops(query)
        if not stops:
            print(f"Keine Haltestelle für '{query}' gefunden.")
            continue
        idx = choose("Treffer:", [f"{s.name} (ID {s.ext_id})" for s in stops])
        if idx is not None:
            return stops[idx]


def show_departures(client: KVBHafasClient) -> None:
    stop = pick_stop(client)
    if not stop:
        return
    count = ask("Anzahl Abfahrten", "10")
    deps = client.station_board(stop.ext_id, max_journeys=int(count) if count.isdigit() else 10)
    if not deps:
        print("Keine Abfahrten.")
        return
    print(f"\nAbfahrten {stop.name}:")
    deps.sort(key=lambda d: (d.platform or "", d.planned))
    for platform, group in groupby(deps, key=lambda d: d.platform):
        # Fallback-Werte kommen schon als "Steig N", echte dPlatf-Werte als "2"/"A".
        print(platform if (platform or "").startswith("Steig") else f"Gleis {platform or '?'}")
        for dep in group:
            marker = "  [AUSFALL]" if dep.cancelled else ""
            delay = ""
            if dep.realtime and dep.realtime != dep.planned:
                delay = f" (Ist: {hhmm(dep.realtime)})"
            print(f"  {hhmm(dep.planned)}{delay}  Linie {dep.line:>4}  -> {dep.direction}{marker}")
        print()


def show_alerts(client: KVBHafasClient) -> None:
    idx = choose("Störungsmeldungen filtern nach:", ["Haltestelle", "Linie", "netzweit (alle)"])
    if idx is None:
        return
    stop = pick_stop(client) if idx == 0 else None
    if idx == 0 and stop is None:
        return
    line = ask("Linie (z.B. 18, 133)") if idx == 1 else None
    if idx == 1 and not line:
        return

    hide_ads = ask("Werbung (cat 99) ausblenden? j/n", "j").lower().startswith("j")
    alerts = client.service_alerts(stop, line=line)
    if hide_ads:
        alerts = [a for a in alerts if a.category != 99]
    scope = stop.name if stop else (f"Linie {line}" if line else "gesamtes Netz")
    print(f"\nMeldungen für {scope}:")
    if not alerts:
        print("- keine")
    for alert in alerts[:10]:
        print(f"- [{alert.valid_from}-{alert.valid_to}] {alert.text}")


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
    cons = client.trip_search(start.ext_id, dest.ext_id, date=date, time=f"{time[:4]}00")
    print(f"\n{start.name} -> {dest.name}:")
    if not cons:
        print("- keine Verbindung gefunden")
    for i, con in enumerate(cons, 1):
        print(f"\n  {i}) {hhmm(con.dep_time)} -> {hhmm(con.arr_time)}  ({con.num_changes} Umstieg{"e" if con.num_changes != 1 else ""})")
        for leg in merge_walks(con.legs):
            if leg.walk:
                dist = f", {leg.dist_m} m" if leg.dist_m else ""
                print(f"     Fußweg {hhmm(leg.dep_time)}-{hhmm(leg.arr_time)} ({leg.to_name}{dist})")
            else:
                platf = f" Gleis {leg.dep_platform}" if leg.dep_platform else ""
                arr_platf = f" Gleis {leg.arr_platform}" if leg.arr_platform else ""
                print(f"     Linie {leg.line} -> {leg.direction}")
                print(f"       ab {hhmm(leg.dep_time)} {leg.from_name}{platf}")
                print(f"       an {hhmm(leg.arr_time)} {leg.to_name}{arr_platf}")


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


def show_nearby(client: KVBHafasClient) -> None:
    try:
        lat = float(ask("Breitengrad (lat)", "50.9375"))
        lon = float(ask("Längengrad (lon)", "6.9603"))
        dist = int(ask("Umkreis in Metern", "500"))
    except ValueError:
        print("Ungültige Koordinaten.")
        return
    stops = client.nearby_stops(lat, lon, max_dist_m=dist)
    print(f"\nIn {dist} m Umkreis:")
    if not stops:
        print("- nichts gefunden")
    for stop in stops:
        print(f"- {stop.name} (ID {stop.ext_id})")


MENU: list[tuple[str, object]] = [
    ("Abfahrten einer Haltestelle", show_departures),
    ("Verbindung suchen", show_trip),
    ("Haltestellen in der Nähe", show_nearby),
    ("Störungsmeldungen", show_alerts),
    ("Beenden", None),
]


def main() -> None:
    client = KVBHafasClient()
    print("KVB HAFAS Client — interaktiv (Enter = Default, leer = zurück)")
    while True:
        idx = choose("Was möchtest du tun?", [label for label, _ in MENU])
        action = MENU[idx][1] if idx is not None else None
        if action is None:
            print("Tschüss.")
            return
        try:
            action(client)
        except Exception as exc:  # Netzfehler/HAFAS-Fehler nicht das Menü killen lassen
            print(f"Fehler: {exc}")


if __name__ == "__main__":
    main()

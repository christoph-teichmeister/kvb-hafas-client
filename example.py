"""Kleines Beispiel: Haltestelle suchen und Live-Abfahrten anzeigen."""

import sys

from kvb_hafas import KVBHafasClient

STOP_QUERY = sys.argv[1] if len(sys.argv) > 1 else "Neumarkt"


def main() -> None:
    client = KVBHafasClient()

    stops = client.find_stops(STOP_QUERY)
    if not stops:
        print(f"Keine Haltestelle für '{STOP_QUERY}' gefunden.")
        return

    stop = stops[0]
    print(f"Haltestelle: {stop.name} (ID {stop.ext_id})\n")

    for dep in client.station_board(stop.ext_id):
        marker = "  [AUSFALL]" if dep.cancelled else ""
        delay = ""
        if dep.realtime and dep.realtime != dep.planned:
            delay = f" (Ist: {dep.realtime})"
        print(f"{dep.planned}{delay}  Linie {dep.line:>4}  -> {dep.direction}{marker}")

    print("\nAktuelle Störungsmeldungen (netzweit, erste 5, ohne Werbung):")
    alerts = [a for a in client.service_alerts() if a.category != 99]
    for alert in alerts[:5]:
        print(f"- [{alert.valid_from}-{alert.valid_to}] {alert.text}")


if __name__ == "__main__":
    main()

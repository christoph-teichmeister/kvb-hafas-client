"""Tests für die reine Darstellungslogik der CLI (keine Netzwerkaufrufe)."""

from cli.format import dur_min
from cli.trips import dedupe_connections, merge_walks
from unittest.mock import MagicMock

from kvb_hafas import Connection, Leg, Stop, TripPage


def _walk(from_name: str, to_name: str, dep: str, arr: str, dist_m: int) -> Leg:
    return Leg(walk=True, from_name=from_name, to_name=to_name, dep_time=dep, arr_time=arr, dist_m=dist_m)


def _ride(line: str, dep: str = "102400", arr: str = "105400") -> Leg:
    return Leg(walk=False, from_name="A", to_name="B", dep_time=dep, arr_time=arr, line=line)


def test_merge_walks_collapses_consecutive_walks():
    # HAFAS zerlegt den Weg zwischen zwei Steigen in mehrere Mini-Abschnitte.
    legs = [
        _ride("16", "121000", "121600"),
        _walk("Ebertplatz Gleis 1", "Ebertplatz Zwischenebene", "121600", "121700", 20),
        _walk("Ebertplatz Zwischenebene", "Ebertplatz Gleis 2", "121700", "121800", 30),
        _ride("18", "121900", "123000"),
    ]

    merged = merge_walks(legs)
    assert [leg.walk for leg in merged] == [False, True, False]
    assert merged[1].dist_m == 50
    assert (merged[1].dep_time, merged[1].arr_time) == ("121600", "121800")
    assert merged[1].to_name == "Ebertplatz Gleis 2"


def test_dedupe_connections_keeps_different_routes():
    def con(line: str) -> Connection:
        return Connection(dep_time="102400", arr_time="105400", num_changes=0, legs=[_ride(line)])

    # Gleiche Eckzeiten, aber andere Linie -> keine Dublette.
    result = dedupe_connections([con("16"), con("16"), con("18")])
    assert [c.legs[0].line for c in result] == ["16", "18"]


def test_dur_min_formats_hafas_duration():
    assert dur_min("000200") == "2 min"
    assert dur_min("011500") == "75 min"
    assert dur_min("") == "—"


def _con(dep, arr):
    return Connection(dep_time=dep, arr_time=arr, num_changes=0, legs=[])


def test_show_trip_pages_forward_then_exits(monkeypatch):
    """Die Menü-Indizes hinter den Verbindungen: frühere / spätere / zurück."""
    from cli import trips

    page1 = TripPage(connections=[_con("120000", "121000"), _con("123000", "124000")], ctx_later="fwd")
    page2 = TripPage(connections=[_con("130000", "131000")], ctx_later="fwd2")
    pages = [page1, page2]
    calls = []

    class FakeClient:
        def trip_page(self, *args, **kwargs):
            calls.append(kwargs.get("scroll_ctx"))
            return pages[len(calls) - 1]

    stop = Stop(name="Köln Neumarkt", ext_id="900000002")
    # Start, Ziel, dann Via = None (leere Eingabe = kein Zwischenhalt).
    stops = iter([stop, stop, None])
    monkeypatch.setattr(trips, "pick_stop", lambda *a, **k: next(stops))
    monkeypatch.setattr(trips, "ask", lambda *a, **k: "20260916")
    monkeypatch.setattr(trips, "pick_products", lambda: None)
    monkeypatch.setattr(trips, "console", MagicMock())
    monkeypatch.setattr(trips, "panel", lambda *a, **k: None)
    monkeypatch.setattr(trips, "connection_tree", lambda con: None)

    # "spätere" auf Seite 1 (Index 2 bei 2 Verbindungen), dann "zurück" auf
    # Seite 2 (Index 3 bei 1 Verbindung).
    choices = iter([3, 3])
    monkeypatch.setattr(trips, "choose", lambda *a, **k: next(choices))

    trips.show_trip(FakeClient())

    assert calls == [None, "fwd"]

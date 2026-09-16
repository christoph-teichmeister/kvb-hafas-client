"""Regressionstest für die Zeitarithmetik in analyze.sql.

Der Anlass: die Abstände wurden ursprünglich über julianday() gerechnet. Das
liefert Fließkomma — eine Minute ergibt 0.99999994 — und CAST(... AS INTEGER)
schneidet das auf 0 ab. Jeder Takt- und Fahrzeitwert war dadurch um bis zu
eine Minute zu klein, ohne dass irgendetwas fehlschlug. Genau solche Fehler
fallen in einer Auswertung nicht auf, deshalb dieser Test.
"""

import pathlib

from kvb_hafas import JourneyRoute, JourneyStop, Stop, storage

ANALYZE_SQL = pathlib.Path(__file__).resolve().parent.parent / "analyze.sql"


def _headway_view(conn):
    """Die headway-View aus analyze.sql anlegen, so wie analyze.py es tut."""
    script = "\n".join(l for l in ANALYZE_SQL.read_text().splitlines() if not l.startswith("."))
    head = script.split("SELECT '— Takt je Stunde")[0]
    conn.executescript(head)


def _journey(jid, dep, idx_stop="300090301"):
    return JourneyRoute(
        jid=jid, line="5", direction="Heumarkt", date="20260917",
        service_days="", service_bits="",
        stops=[JourneyStop(idx=0, stop=Stop(name="Halt", ext_id=idx_stop),
                           dep_planned=dep, arr_planned=None, station_ext_id="900000903")],
    )


def test_headway_is_exact_ten_minutes():
    conn = storage.connect(":memory:")
    for n, dep in enumerate(("080000", "081000", "082000")):
        storage.store_route(conn, _journey(f"j{n}", dep))
    _headway_view(conn)

    takte = [r[0] for r in conn.execute("SELECT takt_min FROM headway ORDER BY abfahrt")]
    assert takte == [10, 10], "Taktabstand muss exakt 10 sein, nicht 9 (julianday-Abschneider)"


def test_headway_across_midnight():
    """Auch über den Tagesübertrag hinweg muss der Abstand stimmen."""
    conn = storage.connect(":memory:")
    storage.store_route(conn, _journey("a", "235000"))
    storage.store_route(conn, _journey("b", "01000000"))  # 00:00 am Folgetag
    _headway_view(conn)

    assert [r[0] for r in conn.execute("SELECT takt_min FROM headway")] == [10]

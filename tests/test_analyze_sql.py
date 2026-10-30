"""Regressionstest für die Zeitarithmetik in timetable/analyze.sql.

Der Anlass: die Abstände wurden ursprünglich über julianday() gerechnet. Das
liefert Fließkomma — eine Minute ergibt 0.99999994 — und CAST(... AS INTEGER)
schneidet das auf 0 ab. Jeder Takt- und Fahrzeitwert war dadurch um bis zu
eine Minute zu klein, ohne dass irgendetwas fehlschlug. Genau solche Fehler
fallen in einer Auswertung nicht auf, deshalb dieser Test.
"""

import pathlib
import sqlite3

import pytest

from kvb_hafas import JourneyRoute, JourneyStop, Stop, storage
from timetable import analyze

ANALYZE_SQL = pathlib.Path(__file__).resolve().parent.parent / "timetable" / "analyze.sql"


def _headway_view(conn):
    """Die headway-View aus analyze.sql anlegen, so wie timetable/analyze.py es tut."""
    script = "\n".join(line for line in ANALYZE_SQL.read_text().splitlines() if not line.startswith("."))
    head = script.split("SELECT '— Takt je Stunde")[0]
    conn.executescript(head)


def _journey(jid, dep, idx_stop="300090301", line="5", direction="Heumarkt", date="20260917"):
    return JourneyRoute(
        jid=jid, line=line, direction=direction, date=date,
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


def test_headway_partitions_by_line():
    """Zwei Linien am selben Mast: der Abstand 5 -> 13 ist kein Takt."""
    conn = storage.connect(":memory:")
    for n, dep in enumerate(("080000", "081000", "082000")):
        storage.store_route(conn, _journey(f"l5-{n}", dep, line="5"))
    for n, dep in enumerate(("080300", "083300")):
        storage.store_route(conn, _journey(f"l13-{n}", dep, line="13"))
    _headway_view(conn)

    rows = conn.execute("SELECT linie, takt_min FROM headway ORDER BY linie, abfahrt").fetchall()
    assert rows == [("13", 30), ("5", 10), ("5", 10)]


def test_headway_partitions_by_service_date():
    """Die erste Fahrt eines Betriebstags hat keinen Vorgänger vom Vortag."""
    conn = storage.connect(":memory:")
    storage.store_route(conn, _journey("d1", "080000", date="20260917"))
    storage.store_route(conn, _journey("d2", "080000", date="20260918"))
    _headway_view(conn)

    assert conn.execute("SELECT COUNT(*) FROM headway").fetchone()[0] == 0


def _db_file(tmp_path):
    db = tmp_path / "timetable.db"
    conn = storage.connect(str(db))
    for n, dep in enumerate(("080000", "081000", "082000")):
        storage.store_route(conn, _journey(f"h{n}", dep, direction="Heumarkt"))
        storage.store_route(conn, _journey(f"b{n}", dep, direction="Am Butzweilerhof", idx_stop="300090302"))
    for n, dep in enumerate(("080500", "083500")):
        storage.store_route(conn, _journey(f"x{n}", dep, line="13", direction="Sülzgürtel"))
    conn.commit()
    conn.close()
    return db


def test_analyze_runs_whole_script_read_only(tmp_path, capsys):
    db = _db_file(tmp_path)
    before = db.read_bytes()

    assert analyze.main(["--db", str(db)]) == 0

    out = capsys.readouterr().out
    assert "Hin- vs. Rückrichtung" in out
    # Richtungsvergleich ohne fest kodierte Namen: Linie 5 mit beiden Richtungen.
    assert "Am Butzweilerhof" in out and "Heumarkt" in out
    assert db.read_bytes() == before, "Auswertung darf die DB nicht verändern"


def test_analyze_missing_db_has_clear_message(tmp_path):
    missing = tmp_path / "gibtsnicht.db"
    with pytest.raises(SystemExit) as exc:
        analyze.main(["--db", str(missing)])
    assert "nicht gefunden" in str(exc.value)
    assert not missing.exists(), "fehlende DB darf nicht leer angelegt werden"


def test_analyze_rejects_non_timetable_db(tmp_path):
    other = tmp_path / "other.db"
    sqlite3.connect(str(other)).close()
    with pytest.raises(SystemExit) as exc:
        analyze.main(["--db", str(other)])
    assert "keine Fahrplan-Datenbank" in str(exc.value)

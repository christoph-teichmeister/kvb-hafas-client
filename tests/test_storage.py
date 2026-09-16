from datetime import date

import pytest

from kvb_hafas import JourneyRoute, JourneyStop, Stop, storage

SERVICE_DATE = date(2026, 9, 17)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("082900", "2026-09-17 08:29:00"),
        ("235900", "2026-09-17 23:59:00"),
        # Tagesübertrag: 00:15 gehört zum Folgetag, nicht an den Tagesanfang
        ("01001500", "2026-09-18 00:15:00"),
        ("02003000", "2026-09-19 00:30:00"),
        ("", None),
        (None, None),
        ("2244", None),
        ("abcdef", None),
    ],
)
def test_parse_hafas_time(value, expected):
    assert storage.parse_hafas_time(value, SERVICE_DATE) == expected


def test_midnight_carry_keeps_order():
    """Ohne aufgelösten Übertrag läge die Nachtfahrt vor der Abendfahrt —
    genau das würde jeden Taktabstand um Mitternacht verfälschen."""
    evening = storage.parse_hafas_time("234500", SERVICE_DATE)
    night = storage.parse_hafas_time("01001500", SERVICE_DATE)
    assert evening < night


def _route(jid="1|1|1|1|17092026", times=(("082900", None), (None, "085600"))):
    stops = [
        JourneyStop(
            idx=i,
            stop=Stop(name=f"Halt {i}", ext_id=f"30000000{i}", lat=50.9 + i, lon=6.9 + i),
            dep_planned=dep,
            arr_planned=arr,
        )
        for i, (dep, arr) in enumerate(times)
    ]
    return JourneyRoute(
        jid=jid,
        line="5",
        direction="Heumarkt",
        date="20260917",
        service_days="Mo - Fr",
        service_bits="00FF",
        stops=stops,
    )


def test_store_route_persists_everything():
    conn = storage.connect(":memory:")
    storage.store_route(conn, _route())

    assert storage.counts(conn) == {"stops": 2, "journeys": 1, "stop_times": 2}
    row = conn.execute("SELECT dep_planned, arr_planned FROM stop_time WHERE idx = 0").fetchone()
    assert row == ("2026-09-17 08:29:00", None)
    assert conn.execute("SELECT stop_count FROM journey").fetchone()[0] == 2


def test_store_route_is_idempotent():
    conn = storage.connect(":memory:")
    storage.store_route(conn, _route())
    storage.store_route(conn, _route())

    assert storage.counts(conn) == {"stops": 2, "journeys": 1, "stop_times": 2}


def test_known_jids_enables_resume():
    conn = storage.connect(":memory:")
    storage.store_route(conn, _route(jid="A"))
    assert storage.known_jids(conn) == {"A"}


def test_store_route_keeps_station_id():
    """Beide Fahrtrichtungen einer Haltestelle haben verschiedene Mast-IDs,
    aber dieselbe station_id — sonst ließen sie sich nicht zusammenfassen."""
    conn = storage.connect(":memory:")
    hin = JourneyRoute(
        jid="hin", line="5", direction="Heumarkt", date="20260917",
        service_days="", service_bits="",
        stops=[JourneyStop(idx=0, stop=Stop(name="Halt", ext_id="300090301"),
                           dep_planned="080000", arr_planned=None, station_ext_id="900000903")],
    )
    rueck = JourneyRoute(
        jid="rueck", line="5", direction="Am Butzweilerhof", date="20260917",
        service_days="", service_bits="",
        stops=[JourneyStop(idx=0, stop=Stop(name="Halt", ext_id="300090302"),
                           dep_planned="081000", arr_planned=None, station_ext_id="900000903")],
    )
    storage.store_route(conn, hin)
    storage.store_route(conn, rueck)

    rows = conn.execute("SELECT ext_id, station_id FROM stop ORDER BY ext_id").fetchall()
    assert rows == [("300090301", "900000903"), ("300090302", "900000903")]
    assert conn.execute("SELECT COUNT(DISTINCT station_id) FROM stop").fetchone()[0] == 1

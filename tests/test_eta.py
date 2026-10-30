"""Tests für die ETA-Rechnung "wann ist die Fahrt an meinem Punkt?" (ohne Netz)."""

from __future__ import annotations

import pytest

from kvb_hafas.models import JourneyRoute, JourneyStop, Stop
from kvb_hafas.server.eta import (
    MAX_ROUTE_DISTANCE_M,
    hafas_timestamp,
    journey_eta,
    passes,
    project_on_path,
)

DATE = "20261030"
# Ein Längengrad-Hundertstel auf 50.9° Breite sind rund 700 m — der Halt-
# abstand hier, die Fahrzeit 2 Min. je Abschnitt.
LAT = 50.9


def _stop(i, lon, arr=None, dep=None, arr_rt=None, dep_rt=None, cancelled=False):
    return JourneyStop(
        idx=i,
        stop=Stop(name=f"Halt {i}", ext_id=f"30000{i}01", lat=LAT, lon=lon),
        arr_planned=arr,
        dep_planned=dep,
        arr_realtime=arr_rt,
        dep_realtime=dep_rt,
        cancelled=cancelled,
    )


def _route(stops, date=DATE):
    return JourneyRoute(jid="jid", line="18", direction="Ost", date=date, service_days="", service_bits="", stops=stops)


def _eastbound():
    """Halt 0 (6.90) 12:00 -> Halt 1 (6.91) 12:02/12:03 -> Halt 2 (6.92) 12:05, Echtzeit +1 ab Halt 1."""
    return _route(
        [
            _stop(0, 6.90, dep="120000"),
            _stop(1, 6.91, arr="120200", dep="120300", arr_rt="120300", dep_rt="120400"),
            _stop(2, 6.92, arr="120500", arr_rt="120600"),
        ]
    )


def ts(hhmmss, date=DATE):
    return hafas_timestamp(date, hhmmss)


def test_hafas_timestamp_handles_day_rollover_and_dst():
    assert ts("01000300") - ts("235900") == 4 * 60
    # 25.10.2026 endet die Sommerzeit: 01:30 MESZ -> 03:30 MEZ sind 3 echte Stunden.
    assert hafas_timestamp("20261025", "033000") - hafas_timestamp("20261025", "013000") == 3 * 3600
    assert hafas_timestamp(DATE, "") is None
    assert hafas_timestamp(DATE, "12xx00") is None


def test_project_on_path_returns_fraction_and_foot():
    dist, frac, foot = project_on_path((LAT + 0.001, 6.905), [(LAT, 6.90), (LAT, 6.91)])
    assert frac == pytest.approx(0.5, abs=1e-6)
    assert foot == pytest.approx((LAT, 6.905))
    assert dist == pytest.approx(111.3, abs=1)


def test_eta_interpolates_between_departure_and_arrival():
    # Mitten zwischen Halt 0 (ab 12:00, keine Echtzeit) und Halt 1 (an 12:03 Ist).
    eta = journey_eta(_eastbound(), {}, (LAT, 6.905), now=ts("115500"))
    assert eta["status"] == "approaching"
    assert eta["eta"] == pytest.approx(ts("120130") * 1000)
    assert eta["between"] == ["Halt 0", "Halt 1"]
    assert eta["distance_m"] == 0
    # Halt 0 hat keine Echtzeit -> der Abschnitt gilt als Fahrplanwert.
    assert eta["realtime"] is False


def test_eta_uses_realtime_when_both_ends_have_it():
    eta = journey_eta(_eastbound(), {}, (LAT, 6.915), now=ts("115500"))
    assert eta["realtime"] is True
    assert eta["eta"] == pytest.approx(ts("120500") * 1000)  # 12:04 + (12:06-12:04)/2
    assert eta["delay_minutes"] == 1


def test_eta_follows_segment_geometry_not_straight_line():
    # Abschnitt 0->1 macht einen Bogen nach Norden; der Punkt liegt am Scheitel.
    detour = [(LAT, 6.90), (LAT + 0.01, 6.90), (LAT + 0.01, 6.91), (LAT, 6.91)]
    segments = {("30000001", "30000101"): detour}  # ext_ids aus _stop()
    eta = journey_eta(_eastbound(), segments, (LAT + 0.01, 6.905), now=ts("115500"))
    assert eta["status"] == "approaching"
    assert eta["between"] == ["Halt 0", "Halt 1"]
    assert eta["distance_m"] == 0


def test_point_off_route_is_not_on_route():
    eta = journey_eta(_eastbound(), {}, (LAT + 0.01, 6.905), now=ts("115500"))  # ~1.1 km nördlich
    assert eta["status"] == "not_on_route"
    assert "eta" not in eta
    assert eta["nearest_stop"]["name"] in {"Halt 0", "Halt 1"}


def test_vehicle_already_past_point_is_passed():
    eta = journey_eta(_eastbound(), {}, (LAT, 6.905), now=ts("121000"))
    assert eta["status"] == "passed"
    assert eta["eta"] == pytest.approx(ts("120130") * 1000)


def test_short_grace_after_pass_still_counts_as_approaching():
    eta = journey_eta(_eastbound(), {}, (LAT, 6.905), now=ts("120140"))
    assert eta["status"] == "approaching"


def test_point_near_stop_yields_a_single_pass():
    # Direkt an Halt 1 liegt der Punkt an beiden angrenzenden Abschnitten.
    found = passes(_eastbound(), {}, (LAT, 6.91))
    assert len(found) == 1


def test_loop_line_picks_next_upcoming_pass():
    # Ringlinie: 0 -> 1 -> 2 (weit weg) -> 3 == wieder an Halt 1.
    route = _route(
        [
            _stop(0, 6.90, dep="120000"),
            _stop(1, 6.91, arr="120200", dep="120200"),
            _stop(2, 6.99, arr="121000", dep="121000"),
            _stop(3, 6.91, arr="122000", dep="122000"),
            _stop(4, 6.90, arr="122200"),
        ]
    )
    point = (LAT, 6.905)
    assert len(passes(route, {}, point)) == 2
    assert journey_eta(route, {}, point, now=ts("115500"))["eta"] == pytest.approx(ts("120100") * 1000)
    later = journey_eta(route, {}, point, now=ts("121500"))
    assert later["status"] == "approaching"
    assert later["eta"] == pytest.approx(ts("122100") * 1000)


def test_trip_over_midnight():
    route = _route([_stop(0, 6.90, dep="235900"), _stop(1, 6.91, arr="01000100")])
    eta = journey_eta(route, {}, (LAT, 6.905), now=ts("235000"))
    assert eta["eta"] == pytest.approx(ts("01000000") * 1000)


def test_cancelled_stop_is_flagged():
    route = _eastbound()
    route.stops[1].cancelled = True
    eta = journey_eta(route, {}, (LAT, 6.905), now=ts("115500"))
    assert eta["cancelled"] is True


def test_nearest_stop_skips_stops_already_served():
    eta = journey_eta(_eastbound(), {}, (LAT, 6.902), now=ts("120100"))
    # Halt 0 (200 m) ist schon abgefahren, also Halt 1 (500 m).
    assert eta["nearest_stop"]["name"] == "Halt 1"
    assert eta["nearest_stop"]["delay_minutes"] == 1
    assert eta["nearest_stop"]["realtime"] is True


def test_threshold_boundary():
    # Knapp innerhalb bzw. außerhalb von MAX_ROUTE_DISTANCE_M nördlich der Strecke.
    deg = MAX_ROUTE_DISTANCE_M / 111_320
    assert journey_eta(_eastbound(), {}, (LAT + deg * 0.95, 6.905), ts("115500"))["status"] == "approaching"
    assert journey_eta(_eastbound(), {}, (LAT + deg * 1.05, 6.905), ts("115500"))["status"] == "not_on_route"

"""Robustheit von KVBHafasClient gegen kaputte/unerwartete Antworten,
Thread-sicheres Throttling, Retries und AID-Erneuerung — HTTP komplett gemockt."""

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from kvb_hafas import KVBHafasClient, KVBHafasError
from kvb_hafas.parsing import LOCAL_TZ


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


def _ok(meth, res):
    return {"svcResL": [{"meth": meth, "err": "OK", "res": res}]}


def _board(stb, prod_l=None, prod_x=0):
    return _ok(
        "StationBoard",
        {
            "common": {"prodL": prod_l if prod_l is not None else [{"name": "5"}]},
            "jnyL": [{"stbStop": stb, "prodX": prod_x, "dirTxt": "Heumarkt", "jid": "j"}],
        },
    )


# --- Throttle ---------------------------------------------------------------


def test_throttle_spaces_requests_across_threads():
    client = KVBHafasClient(min_interval=0.05)
    stamps, ids = [], []
    lock = threading.Lock()

    def post(*_args, **kwargs):
        with lock:
            stamps.append(time.monotonic())
            ids.append(kwargs["json"]["id"])
        return _mock_response(_board({"dTimeS": "120000"}))

    with patch.object(client.session, "post", side_effect=post):
        threads = [threading.Thread(target=client.station_board, args=("900000002",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 4
    # Jeder Thread bekommt seinen eigenen Slot — keine zwei Requests im selben Fenster.
    assert min(gaps) >= 0.045
    assert len(set(ids)) == 5


# --- Unvollständige Antworten -------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},  # kein svcResL
        {"svcResL": []},  # leere Liste
        {"svcResL": [{"meth": "StationBoard", "err": "OK"}]},  # OK, aber kein res
        [],  # gar kein Objekt
    ],
)
def test_incomplete_response_raises_hafas_error(body):
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        with pytest.raises(KVBHafasError):
            client.station_board("900000002")


def test_invalid_json_raises_hafas_error_without_body():
    client = KVBHafasClient()
    resp = _mock_response(None)
    resp.json.side_effect = ValueError("<html>" + "x" * 10_000)
    with patch.object(client.session, "post", return_value=resp):
        with pytest.raises(KVBHafasError) as exc:
            client.station_board("900000002")

    assert len(str(exc.value)) < 200


def test_call_many_keeps_none_for_failed_parts():
    client = KVBHafasClient()
    body = {"svcResL": [{"err": "OK", "res": {"a": 1}}, {"err": "FAIL"}]}
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        assert client._call_many([("X", {}), ("Y", {})]) == [{"a": 1}, None]


# --- Retries ------------------------------------------------------------------


def test_default_session_retries_on_gateway_errors():
    client = KVBHafasClient()
    retry = client.session.get_adapter("https://auskunft.kvb.koeln/gate").max_retries

    assert retry.total == 2
    assert retry.backoff_factor == 0.5
    assert set(retry.status_forcelist) == {502, 503, 504}
    assert "POST" in retry.allowed_methods


def test_custom_session_is_left_untouched():
    session = requests.Session()
    before = session.get_adapter("https://auskunft.kvb.koeln/gate")
    client = KVBHafasClient(session=session)

    assert client.session is session
    assert session.get_adapter("https://auskunft.kvb.koeln/gate") is before
    assert before.max_retries.total == 0


# --- AID-Erneuerung -----------------------------------------------------------

AUTH_ERROR = {"err": "AUTH", "errTxt": "HCI Service: authentication failed", "svcResL": []}


def test_auth_error_refetches_aid_once(monkeypatch):
    monkeypatch.delenv("KVB_HAFAS_AID", raising=False)
    client = KVBHafasClient()
    client._cached_aid = "abgelaufen"
    config = _mock_response({"hciAuth": {"aid": "frisch"}})
    responses = [_mock_response(AUTH_ERROR), _mock_response(_board({"dTimeS": "120000"}))]

    with patch.object(client.session, "get", return_value=config) as get, patch.object(
        client.session, "post", side_effect=responses
    ) as post:
        deps = client.station_board("900000002")

    assert deps[0].planned == "120000"
    assert get.call_count == 1
    aids = [c.kwargs["json"]["auth"]["aid"] for c in post.call_args_list]
    assert aids == ["abgelaufen", "frisch"]


def test_auth_error_in_svc_result_also_refetches(monkeypatch):
    monkeypatch.delenv("KVB_HAFAS_AID", raising=False)
    client = KVBHafasClient()
    client._cached_aid = "abgelaufen"
    config = _mock_response({"hciAuth": {"aid": "frisch"}})
    svc_auth = {"svcResL": [{"meth": "StationBoard", "err": "AUTH"}]}
    responses = [_mock_response(svc_auth), _mock_response(_board({"dTimeS": "120000"}))]

    with patch.object(client.session, "get", return_value=config), patch.object(
        client.session, "post", side_effect=responses
    ) as post:
        client.station_board("900000002")

    assert post.call_count == 2


def test_persistent_auth_error_raises_after_one_retry(monkeypatch):
    monkeypatch.delenv("KVB_HAFAS_AID", raising=False)
    client = KVBHafasClient()
    config = _mock_response({"hciAuth": {"aid": "immer-falsch"}})

    with patch.object(client.session, "get", return_value=config), patch.object(
        client.session, "post", return_value=_mock_response(AUTH_ERROR)
    ) as post:
        with pytest.raises(KVBHafasError, match="AUTH"):
            client.station_board("900000002")

    assert post.call_count == 2


def test_auth_error_with_aid_from_env_does_not_retry():
    # conftest setzt KVB_HAFAS_AID — eine feste AID neu holen bringt nichts.
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(AUTH_ERROR)) as post:
        with pytest.raises(KVBHafasError):
            client.station_board("900000002")

    assert post.call_count == 1


# --- StationBoard -------------------------------------------------------------

BOTH_PREFIXES = {
    "aTimeS": "071500",
    "aTimeR": "071800",
    "aPlatfS": "2",
    "dTimeS": "071700",
    "dTimeR": "071900",
    "dPlatfS": "3",
}


def test_arr_board_prefers_arrival_fields_over_departure_fields():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(_board(BOTH_PREFIXES))):
        arr = client.station_board("900000903", board_type="ARR")[0]
        dep = client.station_board("900000903", board_type="DEP")[0]

    assert (arr.planned, arr.realtime, arr.platform) == ("071500", "071800", "2")
    assert (dep.planned, dep.realtime, dep.platform) == ("071700", "071900", "3")


def test_arr_board_falls_back_to_departure_fields_and_keeps_times_consistent():
    client = KVBHafasClient()
    only_dep = {"dTimeS": "071700", "dTimeR": "071900", "dPlatfR": "4"}
    planned_arr_without_rt = {"aTimeS": "071500", "dTimeS": "071700", "dTimeR": "071900"}
    with patch.object(client.session, "post", return_value=_mock_response(_board(only_dep))):
        start = client.station_board("900000903", board_type="ARR")[0]
    with patch.object(client.session, "post", return_value=_mock_response(_board(planned_arr_without_rt))):
        no_rt = client.station_board("900000903", board_type="ARR")[0]

    assert (start.planned, start.realtime, start.platform) == ("071700", "071900", "4")
    # Keine Ist-Abfahrt neben der Soll-Ankunft.
    assert (no_rt.planned, no_rt.realtime) == ("071500", None)


@pytest.mark.parametrize("prod_l,prod_x", [([{}], 0), ([{"name": "5"}], -1), ([{"name": "5"}], 7), ([], None)])
def test_station_board_line_without_usable_product(prod_l, prod_x):
    client = KVBHafasClient()
    body = _board({"dTimeS": "120000"}, prod_l=prod_l, prod_x=prod_x)
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        assert client.station_board("900000002")[0].line == "?"


def test_station_board_line_falls_back_to_name_s():
    client = KVBHafasClient()
    body = _board({"dTimeS": "120000"}, prod_l=[{"nameS": "RE8"}])
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        assert client.station_board("900000002")[0].line == "RE8"


# --- übrige Methoden ----------------------------------------------------------


def test_find_journeys_defaults_to_cologne_time():
    calls = []

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            return datetime(2026, 10, 30, 23, 30, tzinfo=tz)

    client = KVBHafasClient()
    with patch("kvb_hafas.client.datetime", FakeDatetime), patch.object(
        client.session, "post", return_value=_mock_response(_ok("JourneyMatch", {}))
    ) as post:
        client.find_journeys("18")

    assert calls == [LOCAL_TZ]
    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert (req["date"], req["time"]) == ("20261030", "233000")


@pytest.mark.parametrize("method,meth", [("find_lines", "LineMatch"), ("all_lines", "LineSearch")])
def test_lines_skip_missing_or_negative_prod_index(method, meth):
    body = _ok(
        meth,
        {
            "common": {"prodL": [{"name": "18"}, {"name": "S1"}]},
            "lineL": [{"lineId": "a"}, {"lineId": "b", "prodX": -1}, {"lineId": "c", "prodX": 0}],
        },
    )
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        lines = client.find_lines("18") if method == "find_lines" else client.all_lines()

    assert [(line.name, line.line_id) for line in lines] == [("18", "c")]


def test_walk_route_with_empty_sections():
    client = KVBHafasClient()
    body = _ok("GisRoute", {"conL": [{"dur": "000400", "secL": []}]})
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        route = client.walk_route("ctx")

    assert (route.dist_m, route.duration, route.points) == (0, "000400", [])


def test_affected_stops_needs_both_coordinates():
    client = KVBHafasClient()
    body = _ok("HimMatch", {"affStL": [{"name": "Halb", "extId": "300000101", "crd": {"x": 6950000}}]})
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        stops = client.affected_stops()

    assert (stops[0].lat, stops[0].lon) == (None, None)


def test_find_stops_with_partial_coordinate_has_none():
    client = KVBHafasClient()
    body = _ok("LocMatch", {"match": {"locL": [{"name": "X", "extId": "900000001", "crd": {"y": 50900000}}]}})
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        stop = client.find_stops("X")[0]

    assert (stop.lat, stop.lon) == (None, None)


def test_journey_route_without_idx_uses_position():
    client = KVBHafasClient()
    body = _ok(
        "JourneyDetails",
        {
            "common": {"locL": [{"extId": "300000101"}, {"extId": "300000201"}, {"extId": "300000301"}]},
            "journey": {"stopL": [{"locX": 0}, {"locX": 1}, {"locX": 2}]},
        },
    )
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        route = client.journey_route("j")

    assert [s.idx for s in route.stops] == [0, 1, 2]


# --- _next_stop_delay bei Schleifenfahrten ------------------------------------

LOOP_LOC_L = [{"name": "Schleife"}, {"name": "Mitte"}]
LOOP_JNY = {
    "jid": "loop",
    "date": "20261030",
    "stopL": [
        {"locX": 0, "dTimeS": "120000", "dTimeR": "120100"},  # Start: +1
        {"locX": 1, "aTimeS": "122500", "aTimeR": "122700", "dTimeS": "122500", "dTimeR": "122700"},
        {"locX": 0, "aTimeS": "125000", "aTimeR": "125300"},  # Ende, selber Ort: +3
    ],
}
AT_1251 = datetime(2026, 10, 30, 12, 51, tzinfo=LOCAL_TZ).timestamp()


def test_next_stop_delay_on_loop_uses_stop_after_previous():
    # Fahrzeug kommt von "Mitte" (locX 1) und fährt auf "Schleife" zu: gemeint
    # ist der Endhalt, nicht der gleichnamige Start.
    assert KVBHafasClient._next_stop_delay(LOOP_JNY, LOOP_LOC_L, 0, AT_1251, prev_loc_x=1) == (3, "Schleife")


def test_next_stop_delay_on_loop_without_previous_uses_time():
    assert KVBHafasClient._next_stop_delay(LOOP_JNY, LOOP_LOC_L, 0, AT_1251) == (3, "Schleife")
    at_1159 = datetime(2026, 10, 30, 11, 59, tzinfo=LOCAL_TZ).timestamp()
    assert KVBHafasClient._next_stop_delay(LOOP_JNY, LOOP_LOC_L, 0, at_1159) == (1, "Schleife")


def test_vehicle_positions_passes_previous_stop_for_loops():
    body = _ok(
        "JourneyGeoPos",
        {
            "planrtTS": str(int(AT_1251)),
            "common": {"prodL": [{"name": "Ring"}], "locL": LOOP_LOC_L},
            "jnyL": [
                dict(
                    LOOP_JNY,
                    prodX=0,
                    pos={"x": 6950000, "y": 50930000},
                    ani={"fLocX": [1], "tLocX": [0], "mSec": [], "proc": []},
                )
            ],
        },
    )
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(body)):
        vehicle = client.vehicle_positions(50.8, 6.8, 51.0, 7.1)[0]

    assert (vehicle.delay, vehicle.next_stop) == (3, "Schleife")

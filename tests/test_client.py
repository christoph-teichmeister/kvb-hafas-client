from unittest.mock import MagicMock, patch

import pytest

from kvb_hafas import KVBHafasClient, KVBHafasError, Stop

LOCMATCH_RESPONSE = {
    "svcResL": [
        {
            "meth": "LocMatch",
            "err": "OK",
            "res": {
                "match": {
                    "locL": [
                        {
                            "name": "Köln Neumarkt",
                            "extId": "900000002",
                            "crd": {"x": 6959800, "y": 50936600},
                        }
                    ]
                }
            },
        }
    ]
}

STATIONBOARD_RESPONSE = {
    "svcResL": [
        {
            "meth": "StationBoard",
            "err": "OK",
            "res": {
                "common": {"prodL": [{"name": "146"}]},
                "jnyL": [
                    {
                        "stbStop": {
                            "dTimeS": "210000",
                            "dTimeR": "232400",
                            "dPlatfR": "1",
                        },
                        "prodX": 0,
                        "dirTxt": "Deckstein",
                        "isCncl": False,
                        "jid": "1|2100|1|1|15092026",
                    }
                ],
            },
        }
    ]
}

JOURNEYDETAILS_RESPONSE = {
    "svcResL": [
        {
            "meth": "JourneyDetails",
            "err": "OK",
            "res": {
                "journey": {
                    "stopL": [
                        {"dTimeS": "210000", "dTimeR": "232400", "idx": 0},
                        {"aTimeS": "210300", "idx": 1},
                    ]
                }
            },
        }
    ]
}

LOCGEOPOS_RESPONSE = {
    "svcResL": [
        {
            "meth": "LocGeoPos",
            "err": "OK",
            "res": {
                "locL": [
                    {"name": "Köln Neumarkt", "extId": "900000002", "crd": {"x": 6959800, "y": 50936600}}
                ]
            },
        }
    ]
}

ERROR_RESPONSE = {"svcResL": [{"meth": "StationBoard", "err": "FAIL", "res": {}}]}

ENVELOPE_ERROR_RESPONSE = {"svcResL": [], "err": "PARSE", "id": "abc123"}

HIMSEARCH_RESPONSE = {
    "svcResL": [
        {
            "meth": "HimSearch",
            "err": "OK",
            "res": {
                "msgL": [
                    {
                        "text": "Baumaßnahme im Bereich der Haltestelle Neumarkt.",
                        "cat": 3,
                        "prio": 2,
                        "sDate": "20260910",
                        "eDate": "20270310",
                    },
                    {
                        "text": "Werbung: Jetzt das Deutschlandticket buchen.",
                        "cat": 99,
                        "prio": 99,
                        "sDate": "20260915",
                        "eDate": "20260915",
                    },
                    {
                        "text": "Der Aufzug an der (H) Ulrepforte ist außer Betrieb.",
                        "cat": 1,
                        "prio": 1,
                        "sDate": "20260916",
                        "eDate": "20260916",
                    },
                    {
                        "text": "Hier kein Busverkehr, Umleitung.",
                        "cat": 3,
                        "prio": 1,
                        "sDate": "20260916",
                        "eDate": "20260916",
                        "fLocX": 0,
                        "tLocX": 0,
                    },
                ],
                "common": {
                    "locL": [
                        {"extId": "300051804", "name": "Köln Sportpark Höhenberg", "mMastLocX": 1},
                        {"extId": "900000518", "name": "Köln Sportpark Höhenberg"},
                    ]
                },
            },
        }
    ]
}

TRIPSEARCH_RESPONSE = {
    "svcResL": [
        {
            "meth": "TripSearch",
            "err": "OK",
            "res": {
                "common": {
                    "locL": [
                        {"name": "Köln Neumarkt"},
                        {"name": "Köln Rochusplatz"},
                        {"name": "Köln Ebertplatz"},
                    ],
                    "prodL": [{"name": "1"}, {"name": "Fußweg"}, {"name": "18"}],
                },
                "outConL": [
                    {
                        "dep": {"dTimeS": "120400"},
                        "arr": {"aTimeS": "120700"},
                        "secL": [
                            {
                                "type": "JNY",
                                "dep": {"locX": 0, "dTimeS": "120400", "dPlatfS": "1"},
                                "arr": {"locX": 2, "aTimeS": "120700"},
                                "jny": {"prodX": 0, "dirTxt": "Bensberg"},
                            }
                        ],
                    },
                    {
                        "dep": {"dTimeS": "121400"},
                        "arr": {"aTimeS": "122100"},
                        "secL": [
                            {
                                "type": "JNY",
                                "dep": {"locX": 0, "dTimeS": "121400"},
                                "arr": {"locX": 1, "aTimeS": "121600"},
                                "jny": {"prodX": 0, "dirTxt": "Bensberg"},
                            },
                            # Zwei Mini-Fußwege zwischen den Steigen — dürfen
                            # nicht als Umstieg zählen.
                            {
                                "type": "WALK",
                                "dep": {"locX": 1, "dTimeS": "121600"},
                                "arr": {"locX": 1, "aTimeS": "121700"},
                                "gis": {"dist": 35},
                            },
                            {
                                "type": "WALK",
                                "dep": {"locX": 1, "dTimeS": "121700"},
                                "arr": {"locX": 1, "aTimeS": "121800"},
                                "gis": {"dist": 15},
                            },
                            {
                                "type": "JNY",
                                "dep": {"locX": 1, "dTimeR": "121900", "dTimeS": "121800"},
                                "arr": {"locX": 2, "aTimeS": "122100", "aPlatfR": "2"},
                                "jny": {"prodX": 2, "dirTxt": "Thielenbruch"},
                            },
                        ],
                    },
                ],
            },
        }
    ]
}


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


def test_find_stops_parses_locations():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LOCMATCH_RESPONSE)):
        stops = client.find_stops("Neumarkt")

    assert len(stops) == 1
    assert stops[0].name == "Köln Neumarkt"
    assert stops[0].ext_id == "900000002"
    assert stops[0].lat == pytest.approx(50.9366)
    assert stops[0].lon == pytest.approx(6.9598)


def test_station_board_parses_departures():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)):
        deps = client.station_board("900000002")

    assert len(deps) == 1
    dep = deps[0]
    assert dep.line == "146"
    assert dep.direction == "Deckstein"
    assert dep.planned == "210000"
    assert dep.realtime == "232400"
    assert dep.cancelled is False


def test_call_raises_on_error_code():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(ERROR_RESPONSE)):
        with pytest.raises(KVBHafasError):
            client.station_board("900000002")


def test_station_board_includes_jid():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)):
        deps = client.station_board("900000002")

    assert deps[0].jid == "1|2100|1|1|15092026"


def test_journey_details_returns_stop_list():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYDETAILS_RESPONSE)):
        journey = client.journey_details("1|2100|1|1|15092026")

    assert len(journey["stopL"]) == 2
    assert journey["stopL"][0]["dTimeR"] == "232400"


def test_nearby_stops_parses_locations():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LOCGEOPOS_RESPONSE)):
        stops = client.nearby_stops(lat=50.9366, lon=6.9598)

    assert len(stops) == 1
    assert stops[0].name == "Köln Neumarkt"


def test_service_alerts_parses_messages():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_RESPONSE)):
        alerts = client.service_alerts()

    assert len(alerts) == 4
    assert "Baumaßnahme" in alerts[0].text
    assert alerts[0].category == 3
    assert alerts[1].category == 99  # Werbung — Aufrufer muss selbst filtern


def test_service_alerts_filters_by_stop_name_in_text():
    client = KVBHafasClient()
    stop = Stop(name="Köln Ulrepforte", ext_id="900000019")
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_RESPONSE)):
        alerts = client.service_alerts(stop)

    assert [a.category for a in alerts] == [1]


def test_service_alerts_line_filter_is_sent_server_side():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_RESPONSE)) as post:
        client.service_alerts(line="133")

    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert req["himFltrL"] == [{"type": "LINE", "mode": "INC", "value": "133"}]


def test_service_alerts_without_line_sends_empty_filter():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_RESPONSE)) as post:
        client.service_alerts()

    assert post.call_args.kwargs["json"]["svcReqL"][0]["req"]["himFltrL"] == []


def test_service_alerts_filters_by_master_loc_ext_id():
    client = KVBHafasClient()
    # extId only matches the master loc (locL[1]) that fLocX=0 points to via mMastLocX
    stop = Stop(name="Köln Sportpark Höhenberg", ext_id="900000518")
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_RESPONSE)):
        alerts = client.service_alerts(stop)

    assert [a.text for a in alerts] == ["Hier kein Busverkehr, Umleitung."]


def test_trip_search_parses_connections():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)):
        connections = client.trip_search("900000002", "900000001")

    assert len(connections) == 2
    assert connections[0].dep_time == "120400"
    assert connections[0].num_changes == 0
    # Zwei Fahrten + zwei Fußwege = 1 Umstieg (nicht 3).
    assert connections[1].num_changes == 1

    first = connections[0].legs[0]
    assert (first.walk, first.line, first.direction) == (False, "1", "Bensberg")
    assert (first.from_name, first.to_name) == ("Köln Neumarkt", "Köln Ebertplatz")
    assert first.dep_platform == "1"

    last = connections[1].legs[-1]
    assert last.dep_time == "121900"  # Realtime schlägt Soll
    assert (last.line, last.arr_platform) == ("18", "2")


def test_merge_walks_collapses_consecutive_walks():
    from main import merge_walks

    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)):
        connections = client.trip_search("900000002", "900000001")

    legs = merge_walks(connections[1].legs)
    assert [leg.walk for leg in legs] == [False, True, False]
    assert legs[1].dist_m == 50
    assert (legs[1].dep_time, legs[1].arr_time) == ("121600", "121800")


def test_call_raises_on_envelope_level_error():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(ENVELOPE_ERROR_RESPONSE)):
        with pytest.raises(KVBHafasError):
            client.service_alerts()


def test_stop_lines_dedupes_and_sorts():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)):
        lines = client.stop_lines("900000002")

    assert lines == tuple(sorted(set(lines), key=lambda name: (len(name), name)))
    assert "?" not in lines

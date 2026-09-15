from unittest.mock import MagicMock, patch

import pytest

from kvb_hafas import KVBHafasClient, KVBHafasError

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
                ]
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
                "outConL": [
                    {
                        "dep": {"dTimeS": "120400"},
                        "arr": {"aTimeS": "120700"},
                        "secL": [{"type": "JNY"}],
                    },
                    {
                        "dep": {"dTimeS": "121400"},
                        "arr": {"aTimeS": "122100"},
                        "secL": [{"type": "JNY"}, {"type": "WALK"}, {"type": "JNY"}],
                    },
                ]
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

    assert len(alerts) == 2
    assert "Baumaßnahme" in alerts[0].text
    assert alerts[0].category == 3
    assert alerts[1].category == 99  # Werbung — Aufrufer muss selbst filtern


def test_trip_search_parses_connections():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)):
        connections = client.trip_search("900000002", "900000001")

    assert len(connections) == 2
    assert connections[0].dep_time == "120400"
    assert connections[0].num_changes == 0
    assert connections[1].num_changes == 2


def test_call_raises_on_envelope_level_error():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(ENVELOPE_ERROR_RESPONSE)):
        with pytest.raises(KVBHafasError):
            client.service_alerts()

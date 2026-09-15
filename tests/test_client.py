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

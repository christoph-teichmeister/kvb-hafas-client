from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from kvb_hafas import PRODUCTS, KVBHafasClient, KVBHafasError, Stop

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
                "outCtxScrB": "ctx-back",
                "outCtxScrF": "ctx-forward",
                "outConL": [
                    {
                        "dep": {"dTimeS": "120400"},
                        "arr": {"aTimeS": "120700"},
                        "ovwTrfRefL": [{"fareSetX": 0, "fareX": 1, "type": "F"}],
                        "trfRes": {
                            "fareSetL": [
                                {
                                    "desc": "Rheinlandtarif",
                                    "fareL": [
                                        {"cur": "EUR", "name": "Preisstufe K", "prc": 290},
                                        {"cur": "EUR", "name": "Preisstufe 1b", "prc": 400},
                                    ],
                                }
                            ],
                            "statusCode": "OK",
                        },
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


def test_trip_page_sends_options_and_returns_scroll_ctx():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)) as post:
        page = client.trip_page(
            "900000002",
            "900000001",
            num=6,
            via_ext_id="900000005",
            products=PRODUCTS["stadtbahn"] | PRODUCTS["bus"],
            scroll_ctx="scroll-token",
        )

    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert req["numF"] == 6
    assert req["viaLocL"] == [{"loc": {"extId": "900000005"}}]
    # 2 | 8 = 10, als String — die API nimmt die Bitmaske nicht als Zahl.
    assert req["jnyFltrL"] == [{"type": "PROD", "mode": "INC", "value": "10"}]
    assert req["ctxScr"] == "scroll-token"
    assert page.ctx_later == "ctx-forward"
    assert page.ctx_earlier == "ctx-back"
    assert len(page.connections) == 2


def test_trip_search_omits_unset_options():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)) as post:
        client.trip_search("900000002", "900000001")

    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert set(req) == {"depLocL", "arrLocL"}


def test_trip_search_parses_fare():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)):
        connections = client.trip_search("900000002", "900000001")

    # ovwTrfRefL zeigt auf fareX=1, nicht auf den ersten Eintrag.
    assert (connections[0].fare_cents, connections[0].fare_currency) == (400, "EUR")
    assert connections[0].fare_name == "Preisstufe 1b"
    # Verbindung ohne trfRes bleibt preislos statt zu krachen.
    assert connections[1].fare_cents is None


def _journey_res(line, direction):
    return {
        "common": {"locL": [{"name": "Köln Neumarkt", "extId": "300000202"}], "prodL": [{"name": line}]},
        "journey": {"dirTxt": direction, "prodX": 0, "stopL": [{"locX": 0, "dTimeS": "210000", "idx": 0}]},
    }


JOURNEY_ROUTES_BATCH_RESPONSE = {
    "svcResL": [
        {"meth": "JourneyDetails", "err": "OK", "res": _journey_res("18", "Thielenbruch")},
        {"meth": "JourneyDetails", "err": "FAIL"},
        {"meth": "JourneyDetails", "err": "OK", "res": _journey_res("1", "Bensberg")},
    ]
}


def test_journey_routes_batches_into_one_post():
    client = KVBHafasClient()
    with patch.object(
        client.session, "post", return_value=_mock_response(JOURNEY_ROUTES_BATCH_RESPONSE)
    ) as post:
        routes = client.journey_routes(["jid-a", "jid-b", "jid-c"])

    # Drei Fahrten, ein einziger POST mit drei svcReqL-Einträgen.
    assert post.call_count == 1
    sent = post.call_args.kwargs["json"]["svcReqL"]
    assert [r["req"]["jid"] for r in sent] == ["jid-a", "jid-b", "jid-c"]
    # Die kaputte Teil-Antwort fällt raus, der Rest kommt trotzdem an.
    assert [r.line for r in routes] == ["18", "1"]
    assert routes[0].jid == "jid-a"
    # ... und die zweite Route wird nicht auf die falsche jid gemappt.
    assert routes[1].jid == "jid-c"


def test_journey_routes_splits_into_chunks():
    client = KVBHafasClient()

    def echo(*_args, **kwargs):
        # So viele Teil-Antworten wie Teil-Anfragen — sonst greift die
        # Längenprüfung in _call_many().
        n = len(kwargs["json"]["svcReqL"])
        return _mock_response(
            {"svcResL": [{"meth": "JourneyDetails", "err": "OK", "res": _journey_res("18", "X")}] * n}
        )

    with patch.object(client.session, "post", side_effect=echo) as post:
        routes = client.journey_routes(["a", "b", "c", "d", "e"], chunk=2)

    assert post.call_count == 3  # 2 + 2 + 1
    assert len(routes) == 5


def test_journey_routes_rejects_truncated_batch():
    client = KVBHafasClient()
    truncated = {"svcResL": JOURNEY_ROUTES_BATCH_RESPONSE["svcResL"][:2]}
    with patch.object(client.session, "post", return_value=_mock_response(truncated)):
        # Zwei Antworten auf drei Anfragen: lieber Fehler als Laufwege unter
        # der falschen jid in der DB.
        with pytest.raises(KVBHafasError):
            client.journey_routes(["jid-a", "jid-b", "jid-c"])


def test_call_raises_on_envelope_level_error():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(ENVELOPE_ERROR_RESPONSE)):
        with pytest.raises(KVBHafasError):
            client.service_alerts()


def test_stop_lines_dedupes_and_sorts():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LOCDETAILS_RESPONSE)):
        lines = client.stop_lines("900000002")

    assert lines == ("1", "18", "146")


LOCDETAILS_RESPONSE = {
    "svcResL": [
        {
            "meth": "LocDetails",
            "err": "OK",
            "res": {
                "common": {"prodL": [{"name": "1"}, {"name": "146"}, {"name": "18"}, {"name": "172"}]},
                "locL": [
                    {
                        "name": "Köln Neumarkt",
                        "extId": "900000002",
                        "crd": {"x": 6948329, "y": 50935667},
                        "pRefL": [0, 1, 2],
                    }
                ],
            },
        }
    ]
}

LOCGEOREACH_RESPONSE = {
    "svcResL": [
        {
            "meth": "LocGeoReach",
            "err": "OK",
            "res": {
                "common": {
                    "locL": [
                        {"name": "Steig A", "extId": "300000201", "mMastLocX": 2},
                        {"name": "Steig B", "extId": "300000202", "mMastLocX": 2},
                        {"name": "Köln Poststr.", "extId": "900000003", "crd": {"x": 6950037, "y": 50931541}},
                    ]
                },
                "posL": [
                    {"locX": 0, "dur": 7, "chg": 1},
                    {"locX": 1, "dur": 2, "chg": 0},
                ],
            },
        }
    ]
}

JOURNEYGEOPOS_RESPONSE = {
    "svcResL": [
        {
            "meth": "JourneyGeoPos",
            "err": "OK",
            "res": {
                "common": {
                    "prodL": [{"name": "18", "prodCtx": {"catOut": "Str  "}, "icoX": 1}],
                    "icoL": [
                        {"res": "prod_bus"},
                        {"bg": {"r": 29, "g": 146, "b": 209}, "fg": {"r": 255, "g": 255, "b": 255}},
                    ],
                    "locL": [
                        {"crd": {"x": 6000000, "y": 50000000}, "extId": "300000100", "name": "Anfang"},
                        {"crd": {"x": 6100000, "y": 50200000}, "extId": "300000200", "name": "Ende"},
                    ],
                },
                "jnyL": [
                    {
                        "prodX": 0,
                        "dirTxt": "Thielenbruch",
                        "pos": {"x": 6948068, "y": 50936296},
                        "jid": "1|4809|1|1|16092026",
                        "dirGeo": 27,
                        "stopL": [
                            {"locX": 0, "dTimeS": "120000", "dTimeR": "120000"},
                            {"locX": 1, "aTimeS": "120400", "aTimeR": "120700"},
                        ],
                        "ani": {
                            "mSec": [0, 30000],
                            "proc": [0, 50],
                            "fLocX": [0, 0],
                            "tLocX": [1, 1],
                        },
                    },
                    {"prodX": 0, "dirTxt": "ohne Position"},
                ],
            },
        }
    ]
}

LINEDETAILS_RESPONSE = {
    "svcResL": [
        {
            "meth": "LineDetails",
            "err": "OK",
            "res": {
                "common": {
                    "opL": [{"name": "Kölner Verkehrs-Betriebe"}],
                    "prodL": [
                        {
                            "name": "18",
                            "oprX": 0,
                            "prodCtx": {"catOut": "Str", "lineId": "de:vrs:18"},
                            "stat": {"cnt": 1062, "cncl": 0},
                        }
                    ],
                },
                "line": {},
            },
        }
    ]
}

JOURNEYCOURSE_RESPONSE = {
    "svcResL": [
        {
            "meth": "JourneyCourse",
            "err": "OK",
            "res": {"common": {"polyL": [{"crdEncYX": "ccauH{hei@ulArrA", "delta": True}]}},
        }
    ]
}

SERVERINFO_RESPONSE = {
    "svcResL": [
        {
            "meth": "ServerInfo",
            "err": "OK",
            "res": {"fpB": "20251214", "fpE": "20261212", "sD": "20260916", "sT": "095927"},
        }
    ]
}


def test_server_info_returns_timetable_period():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(SERVERINFO_RESPONSE)):
        info = client.server_info()

    assert (info.timetable_from, info.timetable_to) == ("20251214", "20261212")


def test_stop_details_resolves_line_refs():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LOCDETAILS_RESPONSE)):
        stop = client.stop_details("900000002")

    # pRefL verweist auf prodL — "172" ist nicht referenziert und darf fehlen.
    assert stop.lines == ("1", "18", "146")
    assert stop.lat == pytest.approx(50.935667)


def test_reachable_stops_collapses_platforms_to_master():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LOCGEOREACH_RESPONSE)):
        reachable = client.reachable_stops("900000002", max_minutes=10)

    assert len(reachable) == 1
    assert reachable[0].stop.ext_id == "900000003"
    assert reachable[0].minutes == 2  # schnellster Steig gewinnt


def test_vehicle_positions_skips_entries_without_position():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYGEOPOS_RESPONSE)):
        vehicles = client.vehicle_positions(50.83, 6.75, 51.05, 7.15)

    assert len(vehicles) == 1
    assert vehicles[0].line == "18"
    assert (vehicles[0].lat, vehicles[0].lon) == (pytest.approx(50.936296), pytest.approx(6.948068))


def test_vehicle_positions_interpolates_animation_track():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYGEOPOS_RESPONSE)):
        vehicle = client.vehicle_positions(50.83, 6.75, 51.05, 7.15)[0]

    assert vehicle.category == "Str"
    assert vehicle.bearing == 27
    assert (vehicle.delay, vehicle.next_stop) == (3, "Ende")
    # Linienfarbe kommt aus common.icoL, nicht aus einer eigenen Tabelle.
    assert (vehicle.colour, vehicle.text_colour) == ("#1d92d1", "#ffffff")
    # proc ist in Prozent: 0 = am Starthalt, 50 = halber Weg zum nächsten.
    assert vehicle.track == [
        (0, pytest.approx(50.0), pytest.approx(6.0)),
        (30000, pytest.approx(50.1), pytest.approx(6.05)),
    ]


def test_vehicle_positions_without_animation_track():
    response = deepcopy(JOURNEYGEOPOS_RESPONSE)
    del response["svcResL"][0]["res"]["jnyL"][0]["ani"]
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(response)):
        vehicles = client.vehicle_positions(50.83, 6.75, 51.05, 7.15)

    assert vehicles[0].track == []


def test_line_details_reads_operator_and_stats():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LINEDETAILS_RESPONSE)):
        line = client.line_details("de:vrs:18")

    assert line.category == "Str"
    assert line.operator == "Kölner Verkehrs-Betriebe"
    assert line.journeys == 1062


def test_journey_course_decodes_polyline():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYCOURSE_RESPONSE)):
        points = client.journey_course("1|4809|1|1|16092026")

    assert points[0] == (pytest.approx(50.8013), pytest.approx(6.91358))
    assert len(points) == 2


GISROUTE_RESPONSE = {
    "svcResL": [
        {
            "meth": "GisRoute",
            "err": "OK",
            "res": {
                "common": {"polyL": [{"crdEncYX": "artuHogki@TaE", "delta": True}]},
                "conL": [{"dur": "000200", "secL": [{"type": "WALK", "gis": {"dist": 148}}]}],
            },
        }
    ]
}


def test_walk_route_reads_distance_and_polyline():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(GISROUTE_RESPONSE)):
        route = client.walk_route("H|1|W$…")

    assert route.dist_m == 148
    assert route.duration == "000200"
    assert len(route.points) == 2


def test_trip_search_exposes_walk_gis_ctx():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(TRIPSEARCH_RESPONSE)):
        connections = client.trip_search("900000002", "900000001")

    walks = [leg for con in connections for leg in con.legs if leg.walk]
    # Fahrt-Abschnitte dürfen keinen gis_ctx tragen, Fußwege schon (sofern HAFAS einen liefert).
    rides = [leg for con in connections for leg in con.legs if not leg.walk]
    assert all(leg.gis_ctx == "" for leg in rides)
    assert walks


JOURNEYDETAILS_ROUTE_RESPONSE = {
    "svcResL": [
        {
            "meth": "JourneyDetails",
            "err": "OK",
            "res": {
                "common": {
                    "prodL": [{"name": "5"}],
                    "locL": [
                        {
                            "name": "Köln Ossendorf Sparkasse am Butzweilerhof",
                            "extId": "300090301",
                            "crd": {"x": 6888659, "y": 50984586},
                        },
                        {"name": "Köln Heumarkt", "extId": "300000151", "crd": {"x": 6957453, "y": 50935110}},
                    ],
                },
                "journey": {
                    "jid": "1|9526|16|1|17092026",
                    "prodX": 0,
                    "dirTxt": "Heumarkt",
                    "date": "20260917",
                    "sDaysL": [{"sDaysI": "16. bis 30. Sep 2026 Mo - Fr", "sDaysB": "00FF"}],
                    "stopL": [
                        {"idx": 0, "locX": 0, "dTimeS": "082900"},
                        {"idx": 1, "locX": 1, "aTimeS": "085600"},
                    ],
                },
            },
        }
    ]
}


def test_station_board_passes_date_time_and_type():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)) as post:
        client.station_board("900000002", max_journeys=500, date="20260917", time="040000", board_type="ARR")

    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert req["date"] == "20260917"
    assert req["time"] == "040000"
    assert req["type"] == "ARR"
    assert req["maxJny"] == 500


def test_station_board_omits_date_time_when_not_given():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)) as post:
        client.station_board("900000002")

    req = post.call_args.kwargs["json"]["svcReqL"][0]["req"]
    assert "date" not in req and "time" not in req
    assert req["type"] == "DEP"


def test_station_board_reads_arrival_times_on_arr_board():
    """Auf einer ARR-Tafel steht die Zeit in aTimeS, nicht in dTimeS."""
    arr = {
        "svcResL": [
            {
                "meth": "StationBoard",
                "err": "OK",
                "res": {
                    "common": {"prodL": [{"name": "5"}]},
                    "jnyL": [
                        {
                            "stbStop": {"aTimeS": "071500", "aTimeR": "071800"},
                            "prodX": 0,
                            "dirTxt": "Heumarkt",
                            "jid": "1|9537|0|1|17092026",
                        }
                    ],
                },
            }
        ]
    }
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(arr)):
        deps = client.station_board("900000903", board_type="ARR")

    assert deps[0].planned == "071500"
    assert deps[0].realtime == "071800"


def test_journey_route_resolves_stops_and_service_days():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYDETAILS_ROUTE_RESPONSE)):
        route = client.journey_route("1|9526|16|1|17092026")

    assert route.line == "5"
    assert route.direction == "Heumarkt"
    assert route.date == "20260917"
    assert route.service_days == "16. bis 30. Sep 2026 Mo - Fr"
    assert route.service_bits == "00FF"
    assert [s.stop.ext_id for s in route.stops] == ["300090301", "300000151"]
    assert route.stops[0].stop.name == "Köln Ossendorf Sparkasse am Butzweilerhof"
    assert route.stops[0].stop.lat == pytest.approx(50.984586)
    assert route.stops[0].dep_planned == "082900"
    assert route.stops[0].arr_planned is None
    assert route.stops[1].arr_planned == "085600"


def test_min_interval_throttles_calls(monkeypatch):
    sleeps = []
    monkeypatch.setattr("kvb_hafas.client.time.sleep", sleeps.append)
    client = KVBHafasClient(min_interval=1.0)
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)):
        client.station_board("900000002")
        client.station_board("900000002")

    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0


def test_no_throttle_by_default(monkeypatch):
    sleeps = []
    monkeypatch.setattr("kvb_hafas.client.time.sleep", sleeps.append)
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(STATIONBOARD_RESPONSE)):
        client.station_board("900000002")
        client.station_board("900000002")

    assert sleeps == []


@pytest.mark.parametrize(
    "mast,expected",
    [
        ("300090301", "900000903"),  # Sparkasse am Butzweilerhof
        ("300000151", "900000001"),  # Heumarkt, Steig 51
        ("300097601", "900000976"),  # Ikea am Butzweilerhof
        ("300023901", "900000239"),
        ("900000903", None),  # Master-ID bleibt unangetastet
        ("30009030", None),  # zu kurz
        ("3000903A1", None),  # nicht numerisch
        ("", None),
    ],
)
def test_station_ext_id(mast, expected):
    from kvb_hafas import station_ext_id

    assert station_ext_id(mast) == expected


def test_journey_route_sets_station_ext_id():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYDETAILS_ROUTE_RESPONSE)):
        route = client.journey_route("1|9526|16|1|17092026")

    assert [s.station_ext_id for s in route.stops] == ["900000903", "900000001"]


HIMMATCH_RESPONSE = {
    "svcResL": [
        {
            "meth": "HimMatch",
            "err": "OK",
            "res": {
                "common": {},
                "affStL": [
                    {"name": "Köln Ubierring", "extId": "300001701", "crd": {"x": 0, "y": 0}},
                    {"name": "Köln Ubierring", "extId": "300001701", "crd": {"x": 0, "y": 0}},
                    {"name": "Köln Dellbrück", "extId": "300059504", "crd": {"x": 7060000, "y": 50970000}},
                ],
            },
        }
    ]
}

LINEGEOPOS_RESPONSE = {
    "svcResL": [
        {
            "meth": "LineGeoPos",
            "err": "OK",
            "res": {
                "common": {"prodL": [{"name": "18", "prodCtx": {"catOut": "Str"}}, {"name": "146"}]},
                "lineL": [
                    {"lineId": "de:vrs:18", "prodX": 0},
                    {"lineId": "de:vrs:18", "prodX": 0},
                    {"lineId": "de:vrs:146", "prodX": 1},
                ],
            },
        }
    ]
}


def test_affected_stops_dedupes_and_drops_null_coords():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(HIMMATCH_RESPONSE)):
        stops = client.affected_stops()

    assert [s.ext_id for s in stops] == ["300001701", "300059504"]
    # crd 0/0 ist "keine Koordinate", nicht Position null/null im Atlantik.
    assert stops[0].lat is None
    assert stops[1].lat == pytest.approx(50.97)


LINESEARCH_RESPONSE = {
    "svcResL": [
        {
            "meth": "LineSearch",
            "err": "OK",
            "res": {
                "common": {"prodL": [{"name": "S1"}, {"name": "18", "prodCtx": {"catOut": "Str"}}]},
                "lineL": [
                    {"lineId": "de:nrw:s1:", "prodX": 0},
                    {"lineId": "de:vrs:18", "prodX": 1},
                ],
            },
        }
    ]
}


def test_all_lines_filters_by_prefix():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LINESEARCH_RESPONSE)) as post:
        assert [line.name for line in client.all_lines()] == ["S1", "18"]
        # Katalog geht über die KVB hinaus — Prefix schneidet auf Köln/Bonn zu.
        vrs = client.all_lines("de:vrs")

    assert [(line.name, line.category) for line in vrs] == [("18", "Str")]
    assert post.call_args.kwargs["json"]["svcReqL"][0]["req"] == {}


def test_lines_in_area_dedupes_by_line_id():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(LINEGEOPOS_RESPONSE)):
        lines = client.lines_in_area(50.935667, 6.948329)

    assert [line.name for line in lines] == ["18", "146"]
    assert lines[0].category == "Str"


JOURNEYDETAILS_POLYLINE_RESPONSE = {
    "svcResL": [
        {
            "meth": "JourneyDetails",
            "err": "OK",
            "res": {
                "common": {
                    "locL": [
                        {"extId": "300000100"},
                        {"extId": "300000200"},
                        {"extId": "300000300"},
                    ],
                    "polyL": [
                        {
                            # (50.0, 6.0) -> (50.1, 6.0) -> (50.2, 6.0) -> (50.3, 6.0)
                            "crdEncYX": "_sdpH_{rc@_pR?_pR?_pR?",
                            "ppLocRefL": [
                                {"locX": 0, "ppIdx": 0},
                                {"locX": 1, "ppIdx": 2},
                                {"locX": 2, "ppIdx": 3},
                            ],
                        }
                    ],
                },
                "journey": {},
            },
        }
    ]
}


def test_journey_segments_splits_polyline_per_stop_pair():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYDETAILS_POLYLINE_RESPONSE)):
        segments = client.journey_segments("1|1|1|1|16092026")

    assert sorted(segments) == [("300000100", "300000200"), ("300000200", "300000300")]
    # Der erste Abschnitt behält seinen Zwischenpunkt, der zweite hat keinen.
    assert len(segments[("300000100", "300000200")]) == 3
    assert len(segments[("300000200", "300000300")]) == 2


def test_journey_segments_many_keeps_positions_aligned_with_jids():
    client = KVBHafasClient()
    poly_res = JOURNEYDETAILS_POLYLINE_RESPONSE["svcResL"][0]["res"]
    batch = {
        "svcResL": [
            {"meth": "JourneyDetails", "err": "OK", "res": poly_res},
            {"meth": "JourneyDetails", "err": "FAIL"},
        ]
    }
    with patch.object(client.session, "post", return_value=_mock_response(batch)) as post:
        results = client.journey_segments_many(["jid-a", "jid-b"])

    # Ein POST für beide Fahrten, und getPolyline muss mitgehen — sonst kommen
    # gar keine Abschnitte zurück.
    assert post.call_count == 1
    sent = post.call_args.kwargs["json"]["svcReqL"]
    assert [r["req"] for r in sent] == [
        {"jid": "jid-a", "getPolyline": True},
        {"jid": "jid-b", "getPolyline": True},
    ]
    # Die kaputte Teil-Antwort wird zu {}, damit die Abschnitte der ersten
    # Fahrt nicht der Linie der zweiten zugeschlagen werden.
    assert sorted(results[0]) == [("300000100", "300000200"), ("300000200", "300000300")]
    assert results[1] == {}


def test_vehicle_track_follows_real_geometry_when_segments_are_known():
    # Abschnitt mit einem Knick: die Luftlinie würde die Ecke abschneiden.
    segments = {("300000100", "300000200"): [(50.0, 6.0), (50.0, 6.1), (50.2, 6.1)]}
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(JOURNEYGEOPOS_RESPONSE)):
        vehicle = client.vehicle_positions(50.83, 6.75, 51.05, 7.15, segments=segments)[0]

    # 0 % am Anfang, 50 % in der Mitte des Wegs - plus der Knick als Stützpunkt
    # dazwischen, damit der Browser nicht wieder geradeaus interpoliert.
    assert len(vehicle.track) == 3
    assert vehicle.track[0] == (0, pytest.approx(50.0), pytest.approx(6.0))
    assert vehicle.track[1][1:] == (pytest.approx(50.0), pytest.approx(6.1))
    assert 0 < vehicle.track[1][0] < 30000


def test_service_alerts_carry_stops_and_coordinates():
    client = KVBHafasClient()
    with patch.object(client.session, "post", return_value=_mock_response(HIMSEARCH_GEO_RESPONSE)):
        alerts = client.service_alerts()

    assert alerts[0].stops == ("Köln Sportpark Höhenberg",)
    assert alerts[0].points == [(pytest.approx(50.941537), pytest.approx(7.029268))]
    assert alerts[1].stops == () and alerts[1].points == []  # Aufzugsmeldung ohne Geo-Bezug


HIMSEARCH_GEO_RESPONSE = {
    "svcResL": [
        {
            "meth": "HimSearch",
            "err": "OK",
            "res": {
                "common": {
                    "locL": [{"name": "Köln Sportpark Höhenberg", "crd": {"x": 7029268, "y": 50941537}}],
                    "himMsgEventL": [{"fLocX": 0, "tLocX": 0}],
                    "himMsgEdgeL": [],
                },
                "msgL": [
                    {"text": "Baumaßnahme", "cat": 3, "prio": 1, "eventRefL": [0], "hid": "29225"},
                    {"text": "Aufzug außer Betrieb", "cat": 1, "prio": 1},
                ],
            },
        }
    ]
}

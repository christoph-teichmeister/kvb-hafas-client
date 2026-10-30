"""Tests für den HTTP-Handler des Kartenservers: Routing, Parameterprüfung,
Header, Fehlertexte (echter Server auf 127.0.0.1, KVB-Client gemockt)."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from kvb_hafas.server import http_server as hs


class FakeHistory:
    def __init__(self):
        self.calls = []

    def vehicles(self, **kwargs):
        self.calls.append(("vehicles", kwargs))
        return [{"jid": "j"}]

    def trip(self, jid):
        self.calls.append(("trip", jid))
        return []

    def delay_stats(self, ts_from=None, ts_to=None):
        self.calls.append(("delay_stats", ts_from, ts_to))
        return {"lines": []}

    def max_delay_detail(self, ts_from=None, ts_to=None):
        return None

    def punctuality_by_hour(self, window_seconds, days="all"):
        self.calls.append(("by_hour", window_seconds, days))
        return {"days": days}


@pytest.fixture
def webui(tmp_path, monkeypatch):
    root = tmp_path / "webui"
    (root / "vendor").mkdir(parents=True)
    (root / "index.html").write_text("<h1>index</h1>")
    (root / "shared.css").write_text("body{}")
    (root / "shared.js").write_text("export const x = 1;")
    (root / "vendor" / "lib.js").write_text("var lib;")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setattr(hs, "WEBUI", root)
    return root


@pytest.fixture
def history(monkeypatch):
    fake = FakeHistory()
    monkeypatch.setattr(hs, "HISTORY_ENABLED", True)
    monkeypatch.setattr(hs, "_history", fake)
    monkeypatch.setattr(hs, "_stats_cache", {})
    return fake


@pytest.fixture
def server(webui, monkeypatch):
    monkeypatch.setattr(hs, "_last_viewer_at", float("-inf"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hs.Handler)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp, resp.read()
    finally:
        conn.close()


def get_json(port, path):
    status, _, body = get(port, path)
    return status, json.loads(body)


# --- Routing / statische Dateien -------------------------------------------


def test_root_serves_index_html(server):
    status, resp, body = get(server, "/")
    assert status == 200
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert body == b"<h1>index</h1>"


def test_shared_assets_content_types(server):
    status, resp, body = get(server, "/shared.js")
    assert status == 200
    assert resp.getheader("Content-Type") == "application/javascript; charset=utf-8"
    assert body == b"export const x = 1;"
    status, resp, _ = get(server, "/shared.css")
    assert resp.getheader("Content-Type") == "text/css; charset=utf-8"


def test_unknown_path_is_404(server):
    assert get_json(server, "/nope") == (404, {"error": "not found"})
    assert get_json(server, "/api/nope")[0] == 404


@pytest.mark.parametrize("path", ["/vendor/../secret.txt", "/vendor/../../secret.txt", "/vendor/a/../../index.html", "/vendor/"])
def test_vendor_traversal_rejected(server, path):
    status, _, body = get(server, path)
    assert status == 404
    assert b"secret" not in body


def test_vendor_file_served_with_long_cache(server):
    status, resp, body = get(server, "/vendor/lib.js")
    assert status == 200
    assert body == b"var lib;"
    assert "immutable" in resp.getheader("Cache-Control")


@pytest.mark.parametrize("path", ["/", "/nope", "/api/config", "/vendor/lib.js"])
def test_security_headers_on_every_response(server, path):
    _, resp, _ = get(server, path)
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("Referrer-Policy") == "same-origin"
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")


def test_handler_has_socket_timeout():
    assert hs.Handler.timeout and hs.Handler.timeout <= 60


def test_config_route(server):
    status, body = get_json(server, "/api/config")
    assert status == 200
    assert body["late_from_minutes"] == hs.LATE_FROM


# --- Parameter ---------------------------------------------------------------


def test_parse_bbox():
    assert hs._parse_bbox("50.9,6.9,51.0,7.0") == (50.9, 6.9, 51.0, 7.0)
    for bad in ("", "a,b,c,d", "1,2,3", "51,6.9,50.9,7.0", "50,6,52,7", "nan,6.9,51,7", "inf,6.9,51,7"):
        with pytest.raises(ValueError):
            hs._parse_bbox(bad)


def test_bad_bbox_is_400_without_echoing_input(server):
    status, body = get_json(server, "/api/vehicles?bbox=evil<script>,1,2,3")
    assert status == 400
    assert "evil" not in body["error"]


def test_departures_max_is_clamped(server, monkeypatch):
    seen = []
    monkeypatch.setattr(hs._client, "station_board", lambda stop_id, max_journeys: seen.append(max_journeys) or [])
    assert get_json(server, "/api/departures?stop_id=1&max=999")[0] == 200
    assert get_json(server, "/api/departures?stop_id=1&max=-5")[0] == 200
    assert get_json(server, "/api/departures?stop_id=1")[0] == 200
    assert seen == [hs.MAX_DEPARTURES, 1, 10]
    assert get_json(server, "/api/departures?stop_id=1&max=abc")[0] == 400


def test_stop_search_max_is_clamped(server, monkeypatch):
    seen = []
    monkeypatch.setattr(hs._client, "find_stops", lambda q, max_results: seen.append(max_results) or [])
    assert get_json(server, "/api/stops/search?q=neu&max=1000")[0] == 200
    assert seen == [hs.MAX_STOP_SEARCH_RESULTS]
    assert get_json(server, "/api/stops/search?q=neu&max=1.5")[0] == 400


@pytest.mark.parametrize(
    "query", ["from=abc", "from=-1", "to=99999999999999999999999", "from=1e9", "to=4102444801"]
)
def test_stats_delays_rejects_bad_timestamps(server, history, query):
    status, body = get_json(server, f"/api/stats/delays?{query}")
    assert status == 400
    assert body["error"]
    assert not [c for c in history.calls if c[0] == "delay_stats"]


def test_stats_delays_normalises_timestamps_to_hours(server, history):
    assert get_json(server, "/api/stats/delays?from=7201&to=10799")[0] == 200
    assert get_json(server, "/api/stats/delays?from=7300&to=10000")[0] == 200
    # Gleiche Stunde -> gleicher Cache-Schlüssel -> nur eine Abfrage.
    assert [c for c in history.calls if c[0] == "delay_stats"] == [("delay_stats", 7200, 7200)]


def test_heatmap_days_validated(server, history):
    assert get_json(server, "/api/stats/punctuality-by-hour?days=sat")[1] == {"days": "sat"}
    assert get_json(server, "/api/stats/punctuality-by-hour?days=bogus")[0] == 400


def test_history_vehicles_defaults_to_last_hour(server, history, monkeypatch):
    monkeypatch.setattr(hs.time, "time", lambda: 1_800_000_000.0)
    status, body = get_json(server, "/api/history/vehicles?line=1")
    assert status == 200
    assert history.calls[-1] == ("vehicles", {"line": "1", "ts_from": 1_800_000_000 - 3600, "ts_to": 1_800_000_000})


def test_history_vehicles_window_is_bounded(server, history):
    assert get_json(server, "/api/history/vehicles?from=0&to=999999")[0] == 400
    assert get_json(server, "/api/history/vehicles?from=2000&to=1000")[0] == 400
    assert get_json(server, "/api/history/vehicles?from=1000")[0] == 200
    assert history.calls[-1][1]["ts_to"] == 1000 + 3600


def test_journey_eta_bad_point_is_400(server):
    assert get_json(server, "/api/journey/eta?jid=x&lat=abc&lon=1")[0] == 400


# --- Fehlertexte -------------------------------------------------------------


def test_upstream_errors_are_generic(server, monkeypatch, capsys):
    def boom(stop_id, max_journeys):
        raise OSError("connect to secret-host.internal:443 failed")

    monkeypatch.setattr(hs._client, "station_board", boom)
    status, body = get_json(server, "/api/departures?stop_id=1")
    assert status == 504
    assert body == {"error": "KVB unreachable"}
    assert "secret-host" in capsys.readouterr().err  # Details nur im Log


def test_internal_errors_are_generic(server, monkeypatch):
    monkeypatch.setattr(hs._client, "find_stops", lambda q, max_results: 1 / 0)
    assert get_json(server, "/api/stops/search?q=x") == (500, {"error": "internal error"})


def test_hafas_errors_are_generic(server, monkeypatch):
    def boom(ext_id):
        raise hs.KVBHafasError("H9380 raw details")

    monkeypatch.setattr(hs._client, "stop_details", boom)
    status, body = get_json(server, "/api/stops/details?ext_id=1")
    assert status == 502
    assert "H9380" not in body["error"]


# --- History aus -------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/api/stats/delays", {"error": "history disabled", "lines": [], "least_punctual_lines": []}),
        ("/api/stats/punctuality-trend", {"error": "history disabled", "buckets": [], "lines": {}}),
        ("/api/stats/punctuality-by-hour", {"error": "history disabled", "lines": {}, "counts": {}}),
        ("/api/history/vehicles", {"observations": []}),
        ("/api/history/trip?jid=x", {"observations": []}),
    ],
)
def test_history_disabled_payloads(server, monkeypatch, path, expected):
    monkeypatch.setattr(hs, "HISTORY_ENABLED", False)
    monkeypatch.setattr(hs, "_history", None)
    status, body = get_json(server, path)
    assert status == 200
    for key, value in expected.items():
        assert body[key] == value


def test_history_trip_still_requires_jid_when_disabled(server, monkeypatch):
    monkeypatch.setattr(hs, "HISTORY_ENABLED", False)
    monkeypatch.setattr(hs, "_history", None)
    assert get_json(server, "/api/history/trip")[0] == 400


# --- Viewer-Erkennung --------------------------------------------------------


def test_vehicles_request_marks_viewer_but_not_snapshot(server, monkeypatch):
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_last_vehicle_snapshot", [])
    monkeypatch.setattr(hs, "_last_vehicle_snapshot_at", 0.0)
    monkeypatch.setattr(hs._client, "vehicle_positions", lambda *a, **kw: [])
    assert get_json(server, "/api/vehicles?bbox=50.9,6.9,51.0,7.0")[0] == 200
    assert hs._poll_interval() == hs.VEHICLE_POLL_CACHE_TTL_SECONDS
    assert hs._last_vehicle_snapshot_at == 0.0

"""Tests für die Hintergrund-Threads und Caches des Kartenservers (ohne Netz)."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kvb_hafas.server import http_server as hs


class _Stop(BaseException):
    """Beendet eine Endlosschleife im Test (wird von `except Exception` nicht gefangen)."""


def _stop_after(n):
    calls = []

    def pause(seconds):
        calls.append(seconds)
        if len(calls) >= n:
            raise _Stop

    return pause, calls


# --- Caches ------------------------------------------------------------------


def _vehicle(jid="j1", line="1", direction="Ost", category="Str"):
    return SimpleNamespace(jid=jid, line=line, direction=direction, category=category, colour="", text_colour="")


@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_stats_cache", {})
    monkeypatch.setattr(hs, "_route_queue", hs.deque())
    monkeypatch.setattr(hs, "_routes_queued", set())
    monkeypatch.setattr(hs, "_prefetch_current", [])
    monkeypatch.setattr(hs, "_last_vehicle_snapshot", [])
    monkeypatch.setattr(hs, "_last_vehicle_snapshot_at", 0.0)
    monkeypatch.setattr(hs, "_last_sampled_snapshot_at", 0.0)
    monkeypatch.setattr(hs, "_last_viewer_at", float("-inf"))
    monkeypatch.setattr(hs, "asdict", lambda v: dict(vars(v)))


def test_vehicle_cache_is_bounded_and_drops_expired(fresh_state, monkeypatch):
    monkeypatch.setattr(hs._client, "vehicle_positions", lambda *a, **kw: [])
    monkeypatch.setattr(hs, "_queue_routes", lambda vehicles: None)
    hs._cache[(0.0, 0.0, 1.0, 1.0)] = (hs.time.monotonic() - 10 * hs.VEHICLE_POLL_CACHE_TTL_SECONDS, {})
    for i in range(hs.VEHICLE_CACHE_MAX_ENTRIES + 20):
        hs._vehicles((50.0 + i / 100, 6.9, 50.5 + i / 100, 7.0))
    assert len(hs._cache) == hs.VEHICLE_CACHE_MAX_ENTRIES
    assert (0.0, 0.0, 1.0, 1.0) not in hs._cache
    # Die neuesten bleiben.
    last = tuple(round(v, 2) for v in (50.0 + 83 / 100, 6.9, 50.5 + 83 / 100, 7.0))
    assert last in hs._cache


def test_stats_cache_is_bounded(fresh_state):
    for i in range(hs.STATS_CACHE_MAX_ENTRIES + 10):
        hs._stats_cached(("k", i), 60.0, lambda: {"i": 1})
    assert len(hs._stats_cache) == hs.STATS_CACHE_MAX_ENTRIES
    assert ("k", 0) not in hs._stats_cache


def test_only_poller_writes_snapshot(fresh_state, monkeypatch):
    monkeypatch.setattr(hs._client, "vehicle_positions", lambda *a, **kw: [_vehicle()])
    hs._vehicles((50.9, 6.9, 51.0, 7.0))
    assert hs._last_vehicle_snapshot == [] and hs._last_vehicle_snapshot_at == 0.0
    hs._vehicles((50.9, 6.9, 51.0, 7.0), snapshot=True)  # Cache-Treffer zählt auch
    assert [v["jid"] for v in hs._last_vehicle_snapshot] == ["j1"]
    assert hs._last_vehicle_snapshot_at > 0


# --- Poller ------------------------------------------------------------------


def test_poller_survives_arbitrary_exceptions(fresh_state, monkeypatch, capsys):
    calls = []

    def flaky(bbox, snapshot=False):
        calls.append(snapshot)
        raise RuntimeError("kaputt")

    monkeypatch.setattr(hs, "_vehicles", flaky)
    wait, waits = _stop_after(3)
    monkeypatch.setattr(hs, "_wait_for_next_poll", wait)
    hs._note_viewer()  # volle Rate, damit der Backoff sichtbar wird
    with pytest.raises(_Stop):
        hs._vehicle_snapshot_poller()
    assert calls == [True, True, True]
    assert waits == sorted(waits) and waits[-1] > waits[0]  # Backoff wächst
    assert "RuntimeError: kaputt" in capsys.readouterr().err


def test_poll_interval_slows_down_without_viewers(fresh_state, monkeypatch):
    monkeypatch.setattr(hs, "HISTORY_ENABLED", True)
    idle = hs._poll_interval()
    assert idle == max(hs.VEHICLE_POLL_CACHE_TTL_SECONDS, hs.HISTORY_SAMPLE_INTERVAL_SECONDS)
    monkeypatch.setattr(hs, "HISTORY_ENABLED", False)
    assert hs._poll_interval() >= hs.IDLE_POLL_SECONDS
    hs._note_viewer()
    assert hs._poll_interval() == hs.VEHICLE_POLL_CACHE_TTL_SECONDS
    assert hs._poll_interval(hs.time.monotonic() + hs.VIEWER_IDLE_SECONDS + 1) >= hs.IDLE_POLL_SECONDS


# --- Prefetch ----------------------------------------------------------------


def test_failed_prefetch_batch_is_requeued_then_released(fresh_state, monkeypatch, capsys):
    def boom(jids, chunk):
        raise ValueError("unerwartet")

    monkeypatch.setattr(hs._client, "journey_details_many", boom)
    hs._queue_routes([_vehicle("j1", "1", "Ost"), _vehicle("j2", "5", "West")])
    for attempt in range(1, hs.PREFETCH_MAX_ATTEMPTS):
        with pytest.raises(ValueError):
            hs._prefetch_batch()
        assert [entry[3] for entry in hs._route_queue] == [attempt, attempt]
        assert hs._prefetch_current == []
    with pytest.raises(ValueError):
        hs._prefetch_batch()
    assert not hs._route_queue
    assert hs._routes_queued == set()  # neu einreihbar
    hs._queue_routes([_vehicle("j3", "1", "Ost")])
    assert len(hs._route_queue) == 1


def test_prefetch_success_merges(fresh_state, monkeypatch):
    monkeypatch.setattr(hs, "_segments", {})
    monkeypatch.setattr(hs, "_line_paths", {})
    monkeypatch.setattr(hs, "_save_geometry", lambda: None)
    monkeypatch.setattr(
        hs._client, "journey_details_many", lambda jids, chunk: [({("a", "b"): [(1.0, 1.0), (2.0, 2.0)]}, [])]
    )
    hs._queue_routes([_vehicle("j1", "1", "Ost")])
    assert hs._prefetch_batch() is True
    assert ("a", "b") in hs._segments
    assert hs._prefetch_batch() is False


def test_prefetch_loop_survives_and_backs_off(fresh_state, monkeypatch, capsys):
    def boom():
        raise KeyError("x")

    monkeypatch.setattr(hs, "_prefetch_batch", boom)
    pause, pauses = _stop_after(3)
    monkeypatch.setattr(hs, "_pause", pause)
    with pytest.raises(_Stop):
        hs._prefetch_geometry()
    assert pauses == [5.0, 10.0, 20.0]
    assert "KeyError" in capsys.readouterr().err


# --- Sampler / Purger --------------------------------------------------------


class FakeHistory:
    def __init__(self, fail=False):
        self.recorded = []
        self.purged = []
        self.fail = fail

    def record_vehicles(self, vehicles):
        if self.fail:
            raise RuntimeError("disk full")
        self.recorded.append(vehicles)
        return len(vehicles)

    def purge_older_than(self, days):
        self.purged.append(days)
        return 0


def test_sampler_skips_stale_and_already_recorded_snapshots(fresh_state, monkeypatch):
    store = FakeHistory()
    monkeypatch.setattr(hs, "_history", store)
    monkeypatch.setattr(hs, "HISTORY_FILTER_LOCAL_ONLY", True)
    now = 1_800_000_000.0
    monkeypatch.setattr(hs, "_last_vehicle_snapshot", [{"jid": "t", "category": "Str"}, {"jid": "r", "category": "RE"}])
    monkeypatch.setattr(hs, "_last_vehicle_snapshot_at", now - hs.SNAPSHOT_MAX_AGE_SECONDS - 1)
    assert hs._sample_once(now) == 0
    assert store.recorded == []

    monkeypatch.setattr(hs, "_last_vehicle_snapshot_at", now - 5)
    assert hs._sample_once(now) == 1
    assert store.recorded == [[{"jid": "t", "category": "Str"}]]
    assert hs._sample_once(now + 1) == 0  # derselbe Snapshot nicht doppelt


def test_sampler_loop_survives_errors(fresh_state, monkeypatch, capsys):
    monkeypatch.setattr(hs, "HISTORY_ENABLED", True)
    monkeypatch.setattr(hs, "_history", FakeHistory(fail=True))
    monkeypatch.setattr(hs, "_last_vehicle_snapshot", [{"jid": "t", "category": "Str"}])
    monkeypatch.setattr(hs, "_last_vehicle_snapshot_at", hs.time.time())
    pause, pauses = _stop_after(3)
    monkeypatch.setattr(hs, "_pause", pause)
    with pytest.raises(_Stop):
        hs._history_sampler()
    assert len(pauses) == 3 and pauses[2] > pauses[0]
    assert "RuntimeError: disk full" in capsys.readouterr().err


def test_purger_runs_soon_after_start(fresh_state, monkeypatch):
    store = FakeHistory()
    monkeypatch.setattr(hs, "HISTORY_ENABLED", True)
    monkeypatch.setattr(hs, "_history", store)
    pause, pauses = _stop_after(2)
    monkeypatch.setattr(hs, "_pause", pause)
    with pytest.raises(_Stop):
        hs._history_purger()
    assert pauses == [hs.PURGE_STARTUP_DELAY_SECONDS, hs.PURGE_INTERVAL_SECONDS]
    assert store.purged == [hs.HISTORY_RETENTION_DAYS]


# --- Import / Konfiguration --------------------------------------------------


def test_import_does_not_create_history_db(tmp_path):
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path / "data"),
        "HISTORY_DB_FILE": str(tmp_path / "db" / "history.db"),
        "KVB_HAFAS_AID": "test-aid",
    }
    code = "import kvb_hafas.server.http_server as hs; assert hs._history is None"
    subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, check=True, timeout=60)
    assert not (tmp_path / "db").exists()
    assert not (tmp_path / "data").exists()


def test_get_history_opens_lazily(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "_history", None)
    monkeypatch.setattr(hs, "HISTORY_ENABLED", True)
    monkeypatch.setattr(hs, "DB_FILE", tmp_path / "history.db")
    store = hs._get_history()
    try:
        assert store is hs._get_history()
        assert (tmp_path / "history.db").exists()
    finally:
        store.close()
    monkeypatch.setattr(hs, "_history", None)
    monkeypatch.setattr(hs, "HISTORY_ENABLED", False)
    assert hs._get_history() is None


def test_env_values_respect_minimums(monkeypatch):
    monkeypatch.setenv("X_INT", "1")
    monkeypatch.setenv("X_FLOAT", "0")
    assert hs._env_int("X_INT", 60, minimum=10) == 10
    assert hs._env_float("X_FLOAT", 15.0, minimum=5.0) == 5.0
    monkeypatch.setenv("X_FLOAT", "nan")
    assert hs._env_float("X_FLOAT", 15.0, minimum=5.0) == 15.0
    monkeypatch.setenv("X_INT", "junk")
    assert hs._env_int("X_INT", 60, minimum=10) == 60


# --- rail_geometry.json ------------------------------------------------------


def test_rail_geometry_prefers_data_dir(tmp_path, monkeypatch):
    own = tmp_path / "rail_geometry.json"
    own.write_text('{"a|b": [[1, 2]]}')
    monkeypatch.setattr(hs, "RAIL_FILE", own)
    assert hs._read_rail_geometry() == {"a|b": [[1, 2]]}


def test_rail_geometry_falls_back_to_package_then_warns(tmp_path, monkeypatch, capsys):
    bundled = tmp_path / "pkg.json"
    bundled.write_text('{"c|d": [[3, 4]]}')
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(hs, "_rail_geometry_sources", lambda: [missing, bundled])
    assert hs._read_rail_geometry() == {"c|d": [[3, 4]]}
    monkeypatch.setattr(hs, "_rail_geometry_sources", lambda: [missing])
    assert hs._read_rail_geometry() is None
    assert "rail_geometry.json not found" in capsys.readouterr().err


def test_rail_geometry_sources_include_package_resource(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "RAIL_FILE", tmp_path / "x.json")
    sources = hs._rail_geometry_sources()
    assert sources[0] == tmp_path / "x.json"
    assert str(sources[1]).replace("\\", "/").endswith("kvb_hafas/server/data/rail_geometry.json")

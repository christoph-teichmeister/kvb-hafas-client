"""Tests für den SQLite-Verlaufsspeicher, insbesondere die tram-only-Filterung
und die neuen Detail-/Trend-Abfragen."""

from __future__ import annotations

import time

import pytest

from kvb_hafas.server.history_store import HistoryStore


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "history.db")
    yield s
    s.close()


def _obs(jid, line, category, delay_minutes, direction="Nach Irgendwo", observed_at=None):
    return {
        "jid": jid,
        "line": line,
        "category": category,
        "direction": direction,
        "lat": 50.9,
        "lon": 6.9,
        "bearing": 0,
        "next_stop": "Neumarkt",
        "delay": delay_minutes,
    }


def test_delay_stats_excludes_bus(store):
    now = int(time.time())
    store.record_vehicles([_obs("t1", "1", "Str", 5), _obs("b1", "146", "Bus", 30)], observed_at=now)
    stats = store.delay_stats()
    assert stats["max_delay_minutes"] == 5
    assert [l["line"] for l in stats["lines"]] == ["1"]


def test_max_delay_detail_returns_identity_and_timestamp(store):
    ts = int(time.time())
    store.record_vehicles([_obs("t1", "1", "Str", 2, direction="Bensberg")], observed_at=ts - 100)
    store.record_vehicles([_obs("t2", "3", "Str", 9, direction="Thielenbruch")], observed_at=ts)
    store.record_vehicles([_obs("b1", "146", "Bus", 50)], observed_at=ts)

    detail = store.max_delay_detail()
    assert detail["jid"] == "t2"
    assert detail["line"] == "3"
    assert detail["direction"] == "Thielenbruch"
    assert detail["delay_minutes"] == 9
    assert detail["observed_at"] == ts


def test_max_delay_detail_none_when_no_tram_history(store):
    store.record_vehicles([_obs("b1", "146", "Bus", 10)], observed_at=int(time.time()))
    assert store.max_delay_detail() is None


def test_punctuality_trend_shape_and_gaps(store):
    now = int(time.time())
    day_seconds = 86400
    store.record_vehicles([_obs("t1", "1", "Str", 0)], observed_at=now)
    store.record_vehicles([_obs("t2", "1", "Str", 5)], observed_at=now)  # 1x delayed, 1x on time -> 50%
    store.record_vehicles([_obs("b1", "146", "Bus", 50)], observed_at=now)  # excluded

    trend = store.punctuality_trend(window_seconds=7 * day_seconds, bucket_seconds=day_seconds)
    assert len(trend["buckets"]) == 7
    assert trend["buckets"][-1] == (now // day_seconds) * day_seconds
    assert "146" not in trend["lines"]
    assert trend["lines"]["1"][-1] == 0.5
    # every earlier bucket has no data yet
    assert all(v is None for v in trend["lines"]["1"][:-1])


def test_punctuality_trend_hourly_bucketing(store):
    now = int(time.time())
    hour_seconds = 3600
    store.record_vehicles([_obs("t1", "1", "Str", 10)], observed_at=now)
    store.record_vehicles([_obs("t2", "1", "Str", 0)], observed_at=now - hour_seconds)

    trend = store.punctuality_trend(window_seconds=24 * hour_seconds, bucket_seconds=hour_seconds)
    assert len(trend["buckets"]) == 24
    assert trend["lines"]["1"][-1] == 0.0  # this hour: the one observation was delayed
    assert trend["lines"]["1"][-2] == 1.0  # previous hour: on time

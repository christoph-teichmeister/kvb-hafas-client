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


def test_punctuality_trend_uses_late_from_threshold_and_reports_counts(store):
    now = int(time.time())
    hour_seconds = 3600
    # 2 min late: on time at the default threshold of 3, late at threshold 1
    store.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 3)], observed_at=now)

    trend = store.punctuality_trend(window_seconds=24 * hour_seconds, bucket_seconds=hour_seconds)
    assert trend["late_from_minutes"] == 3
    assert trend["lines"]["1"][-1] == 0.5
    assert trend["counts"]["1"][-1] == 2
    assert trend["counts"]["1"][0] == 0

    strict = store.punctuality_trend(window_seconds=24 * hour_seconds, bucket_seconds=hour_seconds, late_from=1)
    assert strict["lines"]["1"][-1] == 0.0


def test_delay_stats_uses_late_from_threshold(store):
    store.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 5)], observed_at=int(time.time()))
    assert store.delay_stats()["lines"][0]["punctuality_rate"] == 0.5
    assert store.delay_stats(late_from=1)["lines"][0]["punctuality_rate"] == 0.0
    assert store.delay_stats()["late_from_minutes"] == 3


def _berlin():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        return zoneinfo.ZoneInfo("Europe/Berlin")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("tz database not installed")


def test_punctuality_by_hour_buckets_by_local_hour_and_day_type(store, monkeypatch):
    from datetime import datetime

    tz = _berlin()
    # Freeze "now" to a known Sunday so the fixture timestamps stay in the window.
    now = int(datetime(2026, 3, 29, 20, 0, tzinfo=tz).timestamp())
    monkeypatch.setattr(time, "time", lambda: now)

    def ts(*args):
        return int(datetime(*args, tzinfo=tz).timestamp())

    store.record_vehicles([_obs("a", "1", "Str", 0)], observed_at=ts(2026, 3, 27, 8, 15))  # Fri 08h, on time
    store.record_vehicles([_obs("b", "1", "Str", 6)], observed_at=ts(2026, 3, 27, 8, 45))  # Fri 08h, late
    store.record_vehicles([_obs("c", "1", "Str", 0)], observed_at=ts(2026, 3, 28, 8, 30))  # Sat 08h
    # Sun 29.03. is the CEST switch: 03:30 local is after it, still hour 3
    store.record_vehicles([_obs("d", "1", "Str", 9)], observed_at=ts(2026, 3, 29, 3, 30))
    store.record_vehicles([_obs("e", "146", "Bus", 20)], observed_at=ts(2026, 3, 27, 8, 30))  # excluded

    week = 7 * 86400
    all_days = store.punctuality_by_hour(week, tz=tz)
    assert all_days["hours"] == list(range(24))
    assert "146" not in all_days["lines"]
    assert all_days["counts"]["1"][8] == 3
    assert all_days["lines"]["1"][8] == round(2 / 3, 4)
    assert all_days["lines"]["1"][3] == 0.0
    assert all_days["lines"]["1"][0] is None

    weekday = store.punctuality_by_hour(week, tz=tz, days="weekday")
    assert weekday["counts"]["1"][8] == 2
    assert weekday["lines"]["1"][8] == 0.5
    assert weekday["lines"]["1"][3] is None

    assert store.punctuality_by_hour(week, tz=tz, days="sat")["lines"]["1"][8] == 1.0
    assert store.punctuality_by_hour(week, tz=tz, days="sun")["counts"]["1"][3] == 1
    assert store.punctuality_by_hour(week, tz=tz, days="bogus")["days"] == "all"


# ---------------------------------------------------------------------------
# Korruptions-Selbstheilung und Journal-Modus
# ---------------------------------------------------------------------------


def _corrupt_data_pages(path):
    """Überschreibt alles ab Seite 2 mit Müll — Header/Schema (Seite 1) bleibt
    intakt, sodass das Öffnen klappt, Abfragen aber 'malformed' melden."""
    import sqlite3

    page_size = sqlite3.connect(str(path)).execute("PRAGMA page_size").fetchone()[0]
    size = path.stat().st_size
    with open(path, "r+b") as f:
        f.seek(page_size)
        f.write(b"\xde\xad\xbe\xef" * ((size - page_size) // 4))


def _filled_store(path, n=2000):
    s = HistoryStore(path)
    s.record_vehicles([_obs(f"j{i}", "1", "Str", i % 5) for i in range(n)])
    s.close()


def test_uses_wal_journal(store):
    mode = store._run(lambda conn: conn.execute("PRAGMA journal_mode").fetchone()[0])
    sync = store._run(lambda conn: conn.execute("PRAGMA synchronous").fetchone()[0])
    assert mode == "wal"
    assert sync == 1  # NORMAL


def test_garbage_file_is_quarantined_on_open(tmp_path, capsys):
    db = tmp_path / "history.db"
    db.write_bytes(b"not a sqlite file at all" * 100)

    s = HistoryStore(db)
    try:
        assert s.record_vehicles([_obs("j1", "1", "Str", 2)]) == 1
        assert len(s.trip("j1")) == 1
    finally:
        s.close()
    assert (tmp_path / "history.db.corrupt").read_bytes().startswith(b"not a sqlite file")
    assert "corrupt" in capsys.readouterr().err


def test_runtime_corruption_is_quarantined_and_query_retried(tmp_path):
    db = tmp_path / "history.db"
    _filled_store(db)
    _corrupt_data_pages(db)

    s = HistoryStore(db)  # Header intakt -> Öffnen klappt noch
    try:
        assert not (tmp_path / "history.db.corrupt").exists()
        stats = s.delay_stats()  # trifft die kaputten Seiten
        assert stats["lines"] == []  # frische, leere DB statt Exception
        assert (tmp_path / "history.db.corrupt").exists()
        # Store bleibt danach voll benutzbar
        s.record_vehicles([_obs("j1", "7", "Str", 4)])
        assert s.max_delay_detail()["line"] == "7"
    finally:
        s.close()


def test_corrupt_write_is_retried_on_fresh_db(tmp_path):
    db = tmp_path / "history.db"
    _filled_store(db)
    _corrupt_data_pages(db)

    s = HistoryStore(db)
    try:
        assert s.record_vehicles([_obs("j1", "1", "Str", 1)]) == 1
        assert [r["jid"] for r in s.trip("j1")] == ["j1"]
    finally:
        s.close()


def test_only_latest_corrupt_copy_is_kept(tmp_path):
    db = tmp_path / "history.db"
    (tmp_path / "history.db.corrupt").write_bytes(b"old corrupt copy")
    db.write_bytes(b"new garbage" * 100)

    HistoryStore(db).close()
    assert (tmp_path / "history.db.corrupt").read_bytes().startswith(b"new garbage")
    assert sorted(p.name for p in tmp_path.glob("history.db.corrupt*")) == ["history.db.corrupt"]


def test_non_corruption_errors_propagate(store):
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        store._run(lambda conn: conn.execute("SELECT * FROM no_such_table"))


def test_is_corruption_message_fallback():
    """Python < 3.11 kennt kein sqlite_errorcode — dann zählt die Meldung."""
    import sqlite3

    from kvb_hafas.server.history_store import _is_corruption

    assert _is_corruption(sqlite3.DatabaseError("database disk image is malformed"))
    assert _is_corruption(sqlite3.DatabaseError("file is not a database"))
    assert not _is_corruption(sqlite3.DatabaseError("database is locked"))

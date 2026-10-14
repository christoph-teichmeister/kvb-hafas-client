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


def test_punctuality_trend_uses_late_from_threshold_and_reports_counts(store, tmp_path):
    now = int(time.time())
    hour_seconds = 3600
    # 2 min late: on time at the default threshold of 3, late at threshold 1
    store.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 3)], observed_at=now)

    trend = store.punctuality_trend(window_seconds=24 * hour_seconds, bucket_seconds=hour_seconds)
    assert trend["late_from_minutes"] == 3
    assert trend["lines"]["1"][-1] == 0.5
    assert trend["counts"]["1"][-1] == 2
    assert trend["counts"]["1"][0] == 0

    strict = HistoryStore(tmp_path / "strict" / "history.db", late_from=1)
    try:
        strict.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 3)], observed_at=now)
        trend = strict.punctuality_trend(window_seconds=24 * hour_seconds, bucket_seconds=hour_seconds)
        assert trend["lines"]["1"][-1] == 0.0
        assert trend["late_from_minutes"] == 1
    finally:
        strict.close()


def test_punctuality_trend_sub_hour_buckets_read_raw_samples(store):
    now = int(time.time())
    store.record_vehicles([_obs("t1", "1", "Str", 0), _obs("t2", "1", "Str", 4)], observed_at=now)
    store.record_vehicles([_obs("b1", "146", "Bus", 50)], observed_at=now)  # excluded

    trend = store.punctuality_trend(window_seconds=3600, bucket_seconds=300)
    assert len(trend["buckets"]) == 12
    assert list(trend["lines"]) == ["1"]
    assert trend["lines"]["1"][-1] == 0.5
    assert trend["counts"]["1"][-1] == 2


def test_delay_stats_uses_late_from_threshold(store, tmp_path):
    store.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 5)], observed_at=int(time.time()))
    assert store.delay_stats()["lines"][0]["punctuality_rate"] == 0.5
    assert store.delay_stats()["late_from_minutes"] == 3

    strict = HistoryStore(tmp_path / "strict" / "history.db", late_from=1)
    try:
        strict.record_vehicles([_obs("t1", "1", "Str", 2), _obs("t2", "1", "Str", 5)], observed_at=int(time.time()))
        assert strict.delay_stats()["lines"][0]["punctuality_rate"] == 0.0
    finally:
        strict.close()


def test_summary_accumulates_across_samples_in_one_hour(store):
    hour = int(time.time()) // 3600 * 3600
    store.record_vehicles([_obs("t1", "1", "Str", 1), _obs("t2", "1", "Str", 7)], observed_at=hour + 60)
    store.record_vehicles([_obs("t1", "1", "Str", 4)], observed_at=hour + 120)
    store.record_vehicles([_obs("t3", "1", "Str", 7, direction="Später")], observed_at=hour + 180)

    stats = store.delay_stats()
    assert stats["lines"] == [
        {"line": "1", "observations": 4, "avg_delay_minutes": 4.75, "punctuality_rate": 0.25}
    ]
    detail = store.max_delay_detail()
    # equal max: the newer sample wins
    assert (detail["jid"], detail["direction"], detail["observed_at"]) == ("t3", "Später", hour + 180)


def test_delay_stats_time_range_is_hour_granular(store):
    hour = int(time.time()) // 3600 * 3600
    store.record_vehicles([_obs("old", "1", "Str", 9)], observed_at=hour - 7200)
    store.record_vehicles([_obs("new", "1", "Str", 1)], observed_at=hour + 10)

    stats = store.delay_stats(ts_from=hour + 5)  # inside the current hour -> whole hour counts
    assert stats["max_delay_minutes"] == 1
    assert stats["lines"][0]["observations"] == 1
    assert store.max_delay_detail(ts_to=hour - 1)["jid"] == "old"


def test_summary_is_backfilled_from_raw_and_drops_implausible_delays(tmp_path):
    """Upgrade path: an existing history.db without stats.db gets its summary
    built on open, and the pre-cap glitch rows lose their delay."""
    import sqlite3

    db = tmp_path / "history.db"
    s = HistoryStore(db)
    s.record_vehicles([_obs("t1", "12", "Str", 2), _obs("t2", "12", "Str", 4599)], observed_at=int(time.time()))
    s.close()
    (tmp_path / "stats.db").unlink()

    s = HistoryStore(db)
    try:
        stats = s.delay_stats()
        assert stats["max_delay_minutes"] == 2
        assert stats["lines"][0]["observations"] == 1
        assert [r["delay_minutes"] for r in s.trip("t2")] == [None]
    finally:
        s.close()
    rows = sqlite3.connect(str(tmp_path / "stats.db")).execute("SELECT COUNT(*) FROM line_hour_stats").fetchone()
    assert rows == (1,)


def test_summary_survives_purge_of_raw_samples(store):
    store.record_vehicles([_obs("t1", "1", "Str", 5)], observed_at=int(time.time()) - 3 * 86400)
    assert store.purge_older_than(1) == 1
    assert store.trip("t1") == []
    assert store.delay_stats()["lines"][0]["observations"] == 1


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
        assert s.trip("j1") == []  # trifft die kaputten Seiten: frische, leere DB statt Exception
        assert (tmp_path / "history.db.corrupt").exists()
        # stats.db ist eine eigene Datei: die Statistik überlebt die kaputte Rohdatei
        assert s.delay_stats()["lines"][0]["observations"] == 2000
        # Store bleibt danach voll benutzbar
        s.record_vehicles([_obs("j1", "7", "Str", 4)])
        assert [r["line"] for r in s.trip("j1")] == ["7"]
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


def test_outdated_summary_is_rebuilt_with_current_filters(tmp_path):
    """Bumping SUMMARY_VERSION rebuilds stats.db from the raw samples and
    applies a tightened delay cap retroactively."""
    import sqlite3

    from kvb_hafas.server.history_store import SUMMARY_VERSION

    db = tmp_path / "history.db"
    s = HistoryStore(db)
    s.record_vehicles([_obs("t1", "16", "Str", 2), _obs("wrong-trip", "16", "Str", 321)], observed_at=int(time.time()))
    assert s.delay_stats()["max_delay_minutes"] == 321  # write path doesn't cap, parsing does
    s.close()

    conn = sqlite3.connect(str(tmp_path / "stats.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SUMMARY_VERSION
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    s = HistoryStore(db)
    try:
        stats = s.delay_stats()
        assert stats["max_delay_minutes"] == 2
        assert stats["lines"][0]["observations"] == 1
    finally:
        s.close()

    # current version: no rebuild on the next start
    s = HistoryStore(db)
    try:
        s.record_vehicles([_obs("t2", "16", "Str", 4)], observed_at=int(time.time()))
    finally:
        s.close()
    s = HistoryStore(db)
    try:
        assert s.delay_stats()["lines"][0]["observations"] == 2
    finally:
        s.close()

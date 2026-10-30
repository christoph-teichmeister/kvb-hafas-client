"""SQLite time-series store for vehicle history — separate from HA's Recorder,
which isn't built for high-frequency vehicle position sampling.

One table, one row per observed vehicle per sample tick. Sampling itself is
driven by server.py (it reuses whatever the last /api/vehicles poll already
cached — no extra KVB requests are made just for history).
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Callable, TypeVar

from kvb_hafas.parsing import LOCAL_TZ, MAX_PLAUSIBLE_DELAY_MINUTES

_T = TypeVar("_T")

# Mirrors kvb_hafas.webui.is_kvb_local.css_class's substring rules for "tram"
# ("str"/"tram"/"stadtbahn" in the lowercased category) — kept as a SQL LIKE
# fragment since SQLite has no Python-level substring-matching helper to call
# into. Keep these two in sync if the tram-detection rule ever changes.
_TRAM_CATEGORY_SQL = (
    "(LOWER(category) LIKE '%str%' OR LOWER(category) LIKE '%tram%' OR LOWER(category) LIKE '%stadtbahn%')"
)

# Hour-of-day stats are about when a Cologne commuter rides, so they're bucketed
# in Europe/Berlin local time.
_LOCAL_TZ = LOCAL_TZ

# Day-type filters for punctuality_by_hour, as sets of datetime.weekday() values.
DAY_TYPES = {
    "all": frozenset(range(7)),
    "weekday": frozenset(range(5)),
    "sat": frozenset({5}),
    "sun": frozenset({6}),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_observations (
    id INTEGER PRIMARY KEY,
    jid TEXT NOT NULL,
    line TEXT NOT NULL,
    category TEXT NOT NULL,
    direction TEXT,
    lat REAL,
    lon REAL,
    bearing INTEGER,
    next_stop TEXT,
    delay_minutes INTEGER,
    observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vehicle_observations_jid ON vehicle_observations(jid, observed_at);
CREATE INDEX IF NOT EXISTS idx_vehicle_observations_observed_at ON vehicle_observations(observed_at);
-- The observed_at index serves the plain time-range scans; this one the
-- per-line ones (/api/history/vehicles?line=...). IF NOT EXISTS makes it the
-- migration for existing databases too (runs on every open).
CREATE INDEX IF NOT EXISTS idx_vehicle_observations_line_observed_at ON vehicle_observations(line, observed_at);
-- Legacy name of an earlier per-line index.
DROP INDEX IF EXISTS idx_vehicle_observations_line;
"""

# Bump to rebuild stats.db from the raw samples on the next start, e.g. after
# tightening the delay filters (stored as stats.db's PRAGMA user_version).
# 2: delay cap 360 -> 180 min, drops the +321 min wrong-trip samples.
SUMMARY_VERSION = 2

# Tram-only per-line/per-hour aggregates, kept in a separate file (stats.db)
# forever: a few MB per year, so every dashboard stat is a small range scan
# instead of a full scan over the raw samples. The add-on excludes the raw
# history.db from backups but keeps this one.
SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS line_hour_stats (
    line TEXT NOT NULL,
    hour_ts INTEGER NOT NULL,
    n INTEGER NOT NULL,
    late_n INTEGER NOT NULL,
    delay_sum INTEGER NOT NULL,
    max_delay INTEGER NOT NULL,
    max_jid TEXT,
    max_direction TEXT,
    max_observed_at INTEGER,
    PRIMARY KEY (hour_ts, line)
) WITHOUT ROWID;
"""

# Aggregates raw rows with id > ? into line_hour_stats rows (same column
# order). jid/direction/observed_at are SQLite "bare columns": with exactly
# one MAX() in the query they come from the row holding that maximum.
_AGGREGATE_SQL = f"""
SELECT
    line,
    (observed_at / 3600) * 3600 AS hour_ts,
    COUNT(*) AS n,
    SUM(CASE WHEN delay_minutes >= ? THEN 1 ELSE 0 END) AS late_n,
    SUM(delay_minutes) AS delay_sum,
    MAX(delay_minutes) AS max_delay,
    jid,
    direction,
    observed_at
FROM vehicle_observations
WHERE id > ? AND delay_minutes IS NOT NULL AND {_TRAM_CATEGORY_SQL}
GROUP BY line, hour_ts
"""

# On a newer-or-equal max the newer sample wins, matching the old
# "ORDER BY delay DESC, observed_at DESC" tie-break. SQLite evaluates every
# SET expression against the old row, so the CASEs compare with the previous
# max_delay.
_UPSERT_SQL = """
INSERT INTO line_hour_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (hour_ts, line) DO UPDATE SET
    n = n + excluded.n,
    late_n = late_n + excluded.late_n,
    delay_sum = delay_sum + excluded.delay_sum,
    max_jid = CASE WHEN excluded.max_delay >= max_delay THEN excluded.max_jid ELSE max_jid END,
    max_direction = CASE WHEN excluded.max_delay >= max_delay THEN excluded.max_direction ELSE max_direction END,
    max_observed_at = CASE WHEN excluded.max_delay >= max_delay THEN excluded.max_observed_at ELSE max_observed_at END,
    max_delay = MAX(max_delay, excluded.max_delay)
"""

# SQLITE_CORRUPT ("database disk image is malformed") and SQLITE_NOTADB
# ("file is not a database"). Primary result codes — extended codes carry them
# in the low byte.
_CORRUPTION_CODES = {11, 26}


def _rate(n: int, delayed_n: int) -> float | None:
    """Share of on-time observations, rounded; None when there were none."""
    return round(1 - delayed_n / n, 4) if n else None


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back without masking the original error (a corrupt file may fail
    the rollback too)."""
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)  # Python >= 3.11
    if code is not None:
        return (code & 0xFF) in _CORRUPTION_CODES
    msg = str(exc).lower()
    return "malformed" in msg or "not a database" in msg


class _SelfHealingDB:
    """One SQLite file behind one connection and one lock.

    ponytail: one lock guarding one connection is plenty for a small local
    add-on writing at most once a minute; no need for a connection pool.

    Self-healing: the history is a best-effort local statistic, so when SQLite
    reports the file as corrupt (typically after a power loss on SD-card
    storage) the file is moved aside as `<name>.corrupt` and a fresh, empty
    database takes its place instead of failing every request until someone
    deletes it by hand. No full `PRAGMA quick_check` at startup — on a
    year's worth of samples that would scan gigabytes on every boot; corruption
    is detected lazily by whichever query hits it first.
    """

    def __init__(self, db_path: Path, schema: str) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._schema = schema
        self._lock = threading.Lock()
        try:
            self._conn = self._open()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            self._quarantine(exc)
            self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            # WAL + synchronous=NORMAL: a crash or power loss can at worst drop
            # the last few commits instead of leaving a half-written page in the
            # main file. Doesn't help against storage that ignores fsync.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(self._schema)
            conn.commit()
        except BaseException:
            conn.close()
            raise
        return conn

    def _quarantine(self, exc: sqlite3.DatabaseError) -> None:
        """Move the corrupt DB (plus its -wal/-shm sidecars, which would
        otherwise be replayed into the fresh file) to `<name>.corrupt*`.

        Keeps only the most recent corrupt copy, so repeated corruption can't
        fill the disk with multi-GB leftovers.
        """
        for suffix in ("", "-wal", "-shm"):
            src = self._db_path.with_name(self._db_path.name + suffix)
            dst = self._db_path.with_name(self._db_path.name + ".corrupt" + suffix)
            dst.unlink(missing_ok=True)
            if src.exists():
                src.replace(dst)
        print(
            f"[history] {self._db_path} is corrupt ({exc}); moved it to "
            f"{self._db_path.name}.corrupt and started a fresh history database",
            file=sys.stderr,
        )

    def run(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run `fn` against the connection under the lock. On corruption,
        quarantine the file, reopen a fresh DB and retry once."""
        with self._lock:
            try:
                return fn(self._conn)
            except sqlite3.DatabaseError as exc:
                if not _is_corruption(exc):
                    raise
                self._conn.close()
                self._quarantine(exc)
                self._conn = self._open()
                return fn(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class HistoryStore:
    """Raw vehicle samples (`history.db`) plus their tram-only per-line/per-hour
    summary (`stats.db` next to it, see SUMMARY_SCHEMA).

    Every sample write also updates the summary; the dashboard stats read only
    the summary. The two files heal independently, so a corrupt raw file
    doesn't take the long-term stats with it. An observation is late when
    delay_minutes >= `late_from`, fixed per store because the summary counts
    late samples at write time.

    ponytail: changing `late_from` needs a SUMMARY_VERSION bump (rebuild from
    the raw samples). LATE_FROM is a constant today.
    """

    def __init__(self, db_path: Path, late_from: int = 3) -> None:
        self._late_from = late_from
        self._raw = _SelfHealingDB(db_path, SCHEMA)
        self._summary = _SelfHealingDB(db_path.with_name("stats.db"), SUMMARY_SCHEMA)
        self._backfill_summary()

    def _run(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        return self._raw.run(fn)

    def _backfill_summary(self) -> None:
        """(Re)build the summary from all raw samples when it is empty (first
        start after the upgrade, or a lost stats.db) or older than
        SUMMARY_VERSION. Also nulls implausible delays in the raw samples, so
        a tightened cap applies retroactively.

        ponytail: one full scan, blocking startup; ~1 s per 150k samples on a
        Pi. Fine for a one-off; move to a thread if a full year ever needs it.
        """

        def state(conn: sqlite3.Connection) -> tuple[int, bool]:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            return version, conn.execute("SELECT 1 FROM line_hour_stats LIMIT 1").fetchone() is None

        version, empty = self._summary.run(state)
        if version == SUMMARY_VERSION and not empty:
            return

        def aggregate_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            conn.execute(
                "UPDATE vehicle_observations SET delay_minutes = NULL WHERE ABS(delay_minutes) > ?",
                (MAX_PLAUSIBLE_DELAY_MINUTES,),
            )
            conn.commit()
            return conn.execute(_AGGREGATE_SQL, (self._late_from, 0)).fetchall()

        rows = self._raw.run(aggregate_all)

        def rebuild(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM line_hour_stats")
            conn.executemany(_UPSERT_SQL, [tuple(row) for row in rows])
            conn.execute(f"PRAGMA user_version = {SUMMARY_VERSION}")
            conn.commit()

        self._summary.run(rebuild)

    def _upsert_summary(self, rows: list[sqlite3.Row]) -> None:
        if not rows:
            return

        def upsert(conn: sqlite3.Connection) -> None:
            try:
                conn.executemany(_UPSERT_SQL, [tuple(row) for row in rows])
                conn.commit()
            except BaseException:
                _rollback_quietly(conn)
                raise

        self._summary.run(upsert)

    def record_vehicles(self, vehicles: list[dict[str, Any]], observed_at: int | None = None) -> int:
        """Insert one row per vehicle dict (as produced by Vehicle -> asdict())
        and add the new rows to the summary.

        Returns the number of rows inserted.
        """
        if not vehicles:
            return 0
        ts = observed_at if observed_at is not None else int(time.time())
        rows = [
            (
                v.get("jid", ""),
                v.get("line", ""),
                v.get("category", ""),
                v.get("direction"),
                v.get("lat"),
                v.get("lon"),
                v.get("bearing"),
                v.get("next_stop"),
                v.get("delay"),
                ts,
            )
            for v in vehicles
        ]

        summarised = False

        def insert(conn: sqlite3.Connection) -> None:
            # Raw insert and summary upsert succeed or fail together: the raw
            # transaction stays open until the summary has committed and is
            # rolled back if that fails, so a failed write can't leave samples
            # the summary never counted. (Two files, so a hard crash between
            # the two commits remains the only gap.) Lock order is always
            # raw -> summary.
            nonlocal summarised
            try:
                last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM vehicle_observations").fetchone()[0]
                conn.executemany(
                    """INSERT INTO vehicle_observations
                       (jid, line, category, direction, lat, lon, bearing, next_stop, delay_minutes, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                aggregated = conn.execute(_AGGREGATE_SQL, (self._late_from, last_id)).fetchall()
                # A corruption retry of this function on a fresh raw DB must
                # not count the same samples into the summary twice.
                if not summarised:
                    self._upsert_summary(aggregated)
                    summarised = True
                conn.commit()
            except BaseException:
                _rollback_quietly(conn)
                raise

        self._raw.run(insert)
        return len(rows)

    def purge_older_than(self, retention_days: int) -> int:
        """Delete raw observations older than `retention_days`. Returns rows
        removed. The summary is kept forever."""
        cutoff = int(time.time()) - retention_days * 86400

        def delete(conn: sqlite3.Connection) -> int:
            cur = conn.execute("DELETE FROM vehicle_observations WHERE observed_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0

        return self._run(delete)

    def trip(self, jid: str) -> list[dict[str, Any]]:
        rows = self._run(
            lambda conn: conn.execute(
                "SELECT * FROM vehicle_observations WHERE jid = ? ORDER BY observed_at ASC",
                (jid,),
            ).fetchall()
        )
        return [dict(row) for row in rows]

    def vehicles(
        self,
        line: str | None = None,
        ts_from: int | None = None,
        ts_to: int | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM vehicle_observations WHERE 1=1"
        params: list[Any] = []
        if line:
            query += " AND line = ?"
            params.append(line)
        if ts_from is not None:
            query += " AND observed_at >= ?"
            params.append(ts_from)
        if ts_to is not None:
            query += " AND observed_at <= ?"
            params.append(ts_to)
        query += " ORDER BY observed_at ASC LIMIT ?"
        params.append(limit)
        rows = self._run(lambda conn: conn.execute(query, params).fetchall())
        return [dict(row) for row in rows]

    @staticmethod
    def _hour_range(ts_from: int | None, ts_to: int | None) -> tuple[str, list[Any]]:
        """WHERE clause on line_hour_stats.hour_ts. Hour granularity: a bound
        inside an hour includes that whole hour."""
        where = "WHERE 1=1"
        params: list[Any] = []
        if ts_from is not None:
            where += " AND hour_ts >= ?"
            params.append(ts_from // 3600 * 3600)
        if ts_to is not None:
            where += " AND hour_ts <= ?"
            params.append(ts_to)
        return where, params

    def delay_stats(
        self,
        ts_from: int | None = None,
        ts_to: int | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Historical delay aggregations: max delay, per-line averages/punctuality,
        top-N least-punctual lines. Tram-only (KVB Straßenbahn/Stadtbahn), from
        the hourly summary."""
        where, params = self._hour_range(ts_from, ts_to)

        def query(conn: sqlite3.Connection) -> tuple[Any, list[Any]]:
            max_row = conn.execute(f"SELECT MAX(max_delay) AS max_delay FROM line_hour_stats {where}", params).fetchone()
            per_line = conn.execute(
                f"""
                SELECT
                    line,
                    SUM(n) AS n,
                    SUM(delay_sum) * 1.0 / SUM(n) AS avg_delay,
                    SUM(late_n) AS delayed_n
                FROM line_hour_stats
                {where}
                GROUP BY line
                ORDER BY avg_delay DESC
                """,
                params,
            ).fetchall()
            return max_row, per_line

        max_row, per_line = self._summary.run(query)

        lines = []
        for row in per_line:
            n = row["n"] or 0
            delayed_n = row["delayed_n"] or 0
            punctuality_rate = (1 - delayed_n / n) if n else None
            lines.append(
                {
                    "line": row["line"],
                    "observations": n,
                    "avg_delay_minutes": round(row["avg_delay"], 2) if row["avg_delay"] is not None else None,
                    "punctuality_rate": round(punctuality_rate, 4) if punctuality_rate is not None else None,
                }
            )

        least_punctual = sorted(
            (l for l in lines if l["punctuality_rate"] is not None),
            key=lambda l: l["punctuality_rate"],
        )[:top_n]

        return {
            "max_delay_minutes": max_row["max_delay"] if max_row else None,
            "lines": lines,
            "least_punctual_lines": least_punctual,
            "late_from_minutes": self._late_from,
        }

    def max_delay_detail(self, ts_from: int | None = None, ts_to: int | None = None) -> dict[str, Any] | None:
        """Which tram vehicle had the historical max delay, and when.

        Returns None when there's no tram history in range yet.
        """
        where, params = self._hour_range(ts_from, ts_to)
        row = self._summary.run(
            lambda conn: conn.execute(
                f"""SELECT max_jid AS jid, line, max_direction AS direction,
                           max_delay AS delay_minutes, max_observed_at AS observed_at
                    FROM line_hour_stats {where}
                    ORDER BY max_delay DESC, max_observed_at DESC LIMIT 1""",
                params,
            ).fetchone()
        )
        return dict(row) if row else None

    def punctuality_trend(self, window_seconds: int, bucket_seconds: int) -> dict[str, Any]:
        """Tram-only punctuality rate per line, bucketed at `bucket_seconds`
        granularity over the last `window_seconds`.

        Buckets are plain UTC-aligned unix-time slices (`observed_at //
        bucket_seconds * bucket_seconds`) — this covers day/hour/5-min
        granularity uniformly with one query shape. Day-sized buckets land on
        UTC midnight rather than Europe/Berlin midnight; SQLite has no DST-
        aware timezone conversion, and a personal dashboard doesn't need it —
        the up-to-~2h boundary shift is cosmetic, not decision-relevant.

        Whole-hour buckets come from the summary; finer ones (the 1h window)
        from the raw samples via the observed_at index.

        Returns {"buckets": [unix_ts, ...], "lines": {"1": [rate_or_null, ...], ...},
        "counts": {"1": [n, ...], ...}, "late_from_minutes": late_from} with each
        per-line list aligned to "buckets" (oldest first). "counts" is the number
        of observations behind each rate, so the client can flag thin buckets.
        The client formats bucket timestamps into labels appropriate to the
        chosen granularity.
        """
        now = int(time.time())
        now_bucket = (now // bucket_seconds) * bucket_seconds
        n_buckets = window_seconds // bucket_seconds
        buckets = [now_bucket - i * bucket_seconds for i in range(n_buckets - 1, -1, -1)]

        if bucket_seconds % 3600 == 0:
            rows = self._summary.run(
                lambda conn: conn.execute(
                    """
                    SELECT line, (hour_ts / ?) * ? AS bucket, SUM(n) AS n, SUM(late_n) AS delayed_n
                    FROM line_hour_stats
                    WHERE hour_ts >= ?
                    GROUP BY line, bucket
                    """,
                    [bucket_seconds, bucket_seconds, buckets[0]],
                ).fetchall()
            )
        else:
            rows = self._run(
                lambda conn: conn.execute(
                    f"""
                    SELECT
                        line,
                        (observed_at / ?) * ? AS bucket,
                        COUNT(*) AS n,
                        SUM(CASE WHEN delay_minutes >= ? THEN 1 ELSE 0 END) AS delayed_n
                    FROM vehicle_observations
                    WHERE delay_minutes IS NOT NULL AND observed_at >= ? AND {_TRAM_CATEGORY_SQL}
                    GROUP BY line, bucket
                    """,
                    [bucket_seconds, bucket_seconds, self._late_from, now - window_seconds],
                ).fetchall()
            )

        by_line: dict[str, dict[int, tuple[int, int]]] = {}
        for row in rows:
            by_line.setdefault(row["line"], {})[row["bucket"]] = (row["n"] or 0, row["delayed_n"] or 0)

        lines_out: dict[str, list[float | None]] = {}
        counts_out: dict[str, list[int]] = {}
        for line, per_bucket in by_line.items():
            cells = [per_bucket.get(b, (0, 0)) for b in buckets]
            lines_out[line] = [_rate(n, d) for n, d in cells]
            counts_out[line] = [n for n, _ in cells]
        return {"buckets": buckets, "lines": lines_out, "counts": counts_out, "late_from_minutes": self._late_from}

    def punctuality_by_hour(
        self,
        window_seconds: int,
        days: str = "all",
        tz: tzinfo | None = _LOCAL_TZ,
    ) -> dict[str, Any]:
        """Tram-only punctuality per line and local hour of day (0-23) over the
        last `window_seconds`, optionally restricted to a day type
        ("all", "weekday" = Mon-Fri, "sat", "sun"; see DAY_TYPES).

        The summary holds UTC hour slices; Python maps each slice to its local
        weekday/hour. Europe/Berlin offsets are whole hours, so every UTC hour
        slice falls into exactly one local hour — the mapping is exact,
        including across DST switches. Public holidays are not special-cased.

        Returns {"hours": [0..23], "days": days, "late_from_minutes": late_from,
        "lines": {"1": [rate_or_null x24], ...}, "counts": {"1": [n x24], ...}}.
        """
        weekdays = DAY_TYPES.get(days, DAY_TYPES["all"])
        since = int(time.time()) - window_seconds
        rows = self._summary.run(
            lambda conn: conn.execute(
                "SELECT line, hour_ts, n, late_n FROM line_hour_stats WHERE hour_ts >= ?",
                [since // 3600 * 3600],
            ).fetchall()
        )

        totals: dict[str, list[list[int]]] = {}
        for row in rows:
            local = datetime.fromtimestamp(row["hour_ts"], tz)
            if local.weekday() not in weekdays:
                continue
            cell = totals.setdefault(row["line"], [[0, 0] for _ in range(24)])[local.hour]
            cell[0] += row["n"]
            cell[1] += row["late_n"]

        return {
            "hours": list(range(24)),
            "days": days if days in DAY_TYPES else "all",
            "late_from_minutes": self._late_from,
            "lines": {line: [_rate(n, d) for n, d in cells] for line, cells in totals.items()},
            "counts": {line: [n for n, _ in cells] for line, cells in totals.items()},
        }

    def close(self) -> None:
        self._raw.close()
        self._summary.close()

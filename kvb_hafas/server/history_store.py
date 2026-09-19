"""SQLite time-series store for vehicle history — separate from HA's Recorder,
which isn't built for high-frequency vehicle position sampling.

One table, one row per observed vehicle per sample tick. Sampling itself is
driven by server.py (it reuses whatever the last /api/vehicles poll already
cached — no extra KVB requests are made just for history).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# Mirrors kvb_hafas.webui.is_kvb_local.css_class's substring rules for "tram"
# ("str"/"tram"/"stadtbahn" in the lowercased category) — kept as a SQL LIKE
# fragment since SQLite has no Python-level substring-matching helper to call
# into. Keep these two in sync if the tram-detection rule ever changes.
_TRAM_CATEGORY_SQL = (
    "(LOWER(category) LIKE '%str%' OR LOWER(category) LIKE '%tram%' OR LOWER(category) LIKE '%stadtbahn%')"
)

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
CREATE INDEX IF NOT EXISTS idx_vehicle_observations_line ON vehicle_observations(line, observed_at);
"""


class HistoryStore:
    """Thread-safe wrapper around a single SQLite connection.

    ponytail: one lock guarding one connection is plenty for a small local
    add-on writing at most once a minute; no need for a connection pool.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def record_vehicles(self, vehicles: list[dict[str, Any]], observed_at: int | None = None) -> int:
        """Insert one row per vehicle dict (as produced by Vehicle -> asdict()).

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
        with self._lock:
            self._conn.executemany(
                """INSERT INTO vehicle_observations
                   (jid, line, category, direction, lat, lon, bearing, next_stop, delay_minutes, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def purge_older_than(self, retention_days: int) -> int:
        """Delete observations older than `retention_days`. Returns rows removed."""
        cutoff = int(time.time()) - retention_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM vehicle_observations WHERE observed_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount or 0

    def trip(self, jid: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM vehicle_observations WHERE jid = ? ORDER BY observed_at ASC",
                (jid,),
            )
            return [dict(row) for row in cur.fetchall()]

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
        with self._lock:
            cur = self._conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def delay_stats(
        self,
        ts_from: int | None = None,
        ts_to: int | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Historical delay aggregations: max delay, per-line averages/punctuality,
        top-N least-punctual lines. Tram-only (KVB Straßenbahn/Stadtbahn)."""
        where = f"WHERE delay_minutes IS NOT NULL AND {_TRAM_CATEGORY_SQL}"
        params: list[Any] = []
        if ts_from is not None:
            where += " AND observed_at >= ?"
            params.append(ts_from)
        if ts_to is not None:
            where += " AND observed_at <= ?"
            params.append(ts_to)

        with self._lock:
            max_row = self._conn.execute(
                f"SELECT MAX(delay_minutes) AS max_delay FROM vehicle_observations {where}", params
            ).fetchone()
            per_line = self._conn.execute(
                f"""
                SELECT
                    line,
                    COUNT(*) AS n,
                    AVG(delay_minutes) AS avg_delay,
                    SUM(CASE WHEN delay_minutes > 0 THEN 1 ELSE 0 END) AS delayed_n
                FROM vehicle_observations
                {where}
                GROUP BY line
                ORDER BY avg_delay DESC
                """,
                params,
            ).fetchall()

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
        }

    def max_delay_detail(self, ts_from: int | None = None, ts_to: int | None = None) -> dict[str, Any] | None:
        """Which tram vehicle had the historical max delay, and when.

        Returns None when there's no tram history in range yet.
        """
        where = f"WHERE delay_minutes IS NOT NULL AND {_TRAM_CATEGORY_SQL}"
        params: list[Any] = []
        if ts_from is not None:
            where += " AND observed_at >= ?"
            params.append(ts_from)
        if ts_to is not None:
            where += " AND observed_at <= ?"
            params.append(ts_to)
        with self._lock:
            row = self._conn.execute(
                f"""SELECT jid, line, direction, delay_minutes, observed_at
                    FROM vehicle_observations {where}
                    ORDER BY delay_minutes DESC, observed_at DESC LIMIT 1""",
                params,
            ).fetchone()
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

        Returns {"buckets": [unix_ts, ...], "lines": {"1": [rate_or_null, ...], ...}}
        with each per-line list aligned to "buckets" (oldest first). The
        client formats bucket timestamps into labels appropriate to the
        chosen granularity.
        """
        since = int(time.time()) - window_seconds
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    line,
                    (observed_at / ?) * ? AS bucket,
                    COUNT(*) AS n,
                    SUM(CASE WHEN delay_minutes > 0 THEN 1 ELSE 0 END) AS delayed_n
                FROM vehicle_observations
                WHERE delay_minutes IS NOT NULL AND observed_at >= ? AND {_TRAM_CATEGORY_SQL}
                GROUP BY line, bucket
                ORDER BY line, bucket
                """,
                [bucket_seconds, bucket_seconds, since],
            ).fetchall()

        now_bucket = (int(time.time()) // bucket_seconds) * bucket_seconds
        n_buckets = window_seconds // bucket_seconds
        buckets = [now_bucket - i * bucket_seconds for i in range(n_buckets - 1, -1, -1)]

        by_line: dict[str, dict[int, float]] = {}
        for row in rows:
            n = row["n"] or 0
            delayed_n = row["delayed_n"] or 0
            rate = (1 - delayed_n / n) if n else None
            by_line.setdefault(row["line"], {})[row["bucket"]] = round(rate, 4) if rate is not None else None

        lines_out = {line: [rates.get(b) for b in buckets] for line, rates in by_line.items()}
        return {"buckets": buckets, "lines": lines_out}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

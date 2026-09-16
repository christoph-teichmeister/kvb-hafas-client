"""SQLite-Persistenz für erfasste Soll-Fahrpläne.

Bewusst ohne ORM: drei Tabellen, `CREATE TABLE IF NOT EXISTS`, fertig. Die
Auswertung passiert in SQL (siehe analyze.sql), nicht in Python.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date
from datetime import datetime, timedelta

from kvb_hafas.models import JourneyRoute

SCHEMA = """
CREATE TABLE IF NOT EXISTS stop (
    ext_id     TEXT PRIMARY KEY,  -- Mast-ID (300xxxxxNN), richtungsscharf
    station_id TEXT,              -- Master-Haltestelle (900xxxxxx), beide Richtungen
    name       TEXT NOT NULL,
    lat        REAL,
    lon        REAL
);

CREATE INDEX IF NOT EXISTS ix_stop_station ON stop(station_id);

CREATE TABLE IF NOT EXISTS journey (
    jid          TEXT PRIMARY KEY,
    service_date TEXT NOT NULL,   -- YYYY-MM-DD, Betriebstag
    line         TEXT NOT NULL,
    direction    TEXT NOT NULL,   -- Zielhaltestelle laut dirTxt
    service_days TEXT,            -- sDaysI, Klartext
    service_bits TEXT,            -- sDaysB, Bitmaske ab fpB
    stop_count   INTEGER,         -- Halte im Laufweg; trennt Kurzläufer von Vollläufen
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stop_time (
    jid         TEXT NOT NULL REFERENCES journey(jid) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,   -- Position im Laufweg, 0 = Start
    stop_ext_id TEXT NOT NULL REFERENCES stop(ext_id),
    arr_planned TEXT,               -- ISO8601 lokal, Tagesübertrag aufgelöst
    dep_planned TEXT,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (jid, idx)
);

CREATE INDEX IF NOT EXISTS ix_stop_time_stop ON stop_time(stop_ext_id, dep_planned);
CREATE INDEX IF NOT EXISTS ix_stop_time_jid  ON stop_time(jid);
"""


def parse_hafas_time(value: str | None, service_date: _date) -> str | None:
    """HAFAS-Zeitstring -> ISO8601-Zeitstempel, Tagesübertrag aufgelöst.

    HAFAS liefert `"HHMMSS"`, bei Fahrten über Mitternacht mit einem
    Tages-Präfix: `"01001500"` = 00:15 am *Folgetag*. Das Präfix einfach
    abzuschneiden (wie es die Anzeige in main.py tut) würde die Nachtfahrt
    rechnerisch an den Tagesanfang setzen und jeden Taktabstand um
    Mitternacht verfälschen — deshalb wird es hier ausgewertet.
    """
    if not value:
        return None
    digits = value.strip()
    if not digits.isdigit() or len(digits) < 6:
        return None
    day_offset = int(digits[:-6]) if len(digits) > 6 else 0
    hh, mm, ss = int(digits[-6:-4]), int(digits[-4:-2]), int(digits[-2:])
    stamp = datetime.combine(service_date, datetime.min.time()) + timedelta(
        days=day_offset, hours=hh, minutes=mm, seconds=ss
    )
    return stamp.isoformat(sep=" ")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def store_route(conn: sqlite3.Connection, route: JourneyRoute) -> int:
    """Einen kompletten Laufweg ablegen. Idempotent — erneutes Speichern
    derselben Fahrt aktualisiert, statt zu duplizieren."""
    service_date = datetime.strptime(route.date, "%Y%m%d").date()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")

    conn.executemany(
        """INSERT INTO stop (ext_id, station_id, name, lat, lon) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(ext_id) DO UPDATE SET station_id=excluded.station_id,
                                             name=excluded.name,
                                             lat=excluded.lat, lon=excluded.lon""",
        [
            (s.stop.ext_id, s.station_ext_id, s.stop.name, s.stop.lat, s.stop.lon)
            for s in route.stops
            if s.stop.ext_id
        ],
    )
    conn.execute(
        """INSERT INTO journey (jid, service_date, line, direction, service_days,
                                service_bits, stop_count, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(jid) DO UPDATE SET service_date=excluded.service_date,
                                          line=excluded.line,
                                          direction=excluded.direction,
                                          service_days=excluded.service_days,
                                          service_bits=excluded.service_bits,
                                          stop_count=excluded.stop_count,
                                          fetched_at=excluded.fetched_at""",
        (
            route.jid,
            service_date.isoformat(),
            route.line,
            route.direction,
            route.service_days,
            route.service_bits,
            len(route.stops),
            now,
        ),
    )
    conn.executemany(
        """INSERT INTO stop_time (jid, idx, stop_ext_id, arr_planned, dep_planned, cancelled)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(jid, idx) DO UPDATE SET stop_ext_id=excluded.stop_ext_id,
                                               arr_planned=excluded.arr_planned,
                                               dep_planned=excluded.dep_planned,
                                               cancelled=excluded.cancelled""",
        [
            (
                route.jid,
                s.idx,
                s.stop.ext_id,
                parse_hafas_time(s.arr_planned, service_date),
                parse_hafas_time(s.dep_planned, service_date),
                int(s.cancelled),
            )
            for s in route.stops
            if s.stop.ext_id
        ],
    )
    return len(route.stops)


def known_jids(conn: sqlite3.Connection) -> set[str]:
    """Fahrten, deren Laufweg schon in der DB liegt — für Wiederaufnahme
    nach einem Abbruch."""
    return {row[0] for row in conn.execute("SELECT DISTINCT jid FROM stop_time")}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "stops": one("SELECT COUNT(*) FROM stop"),
        "journeys": one("SELECT COUNT(*) FROM journey"),
        "stop_times": one("SELECT COUNT(*) FROM stop_time"),
    }

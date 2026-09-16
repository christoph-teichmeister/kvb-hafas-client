#!/usr/bin/env python3
"""timetable/analyze.sql ausführen und die Ergebnisblöcke ausgeben.

    uv run -m timetable.analyze --db timetable.db

Nur nötig, weil das `sqlite3`-Kommandozeilentool nicht überall installiert
ist; mit CLI tut es auch `sqlite3 -box timetable.db < timetable/analyze.sql`.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3

DEFAULT_SQL = pathlib.Path(__file__).with_name("analyze.sql")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="timetable.db")
    ap.add_argument("--sql", default=str(DEFAULT_SQL))
    args = ap.parse_args()

    script = pathlib.Path(args.sql).read_text()
    # Dot-Commands (.mode, .headers) versteht nur die CLI, nicht das Modul.
    script = "\n".join(line for line in script.splitlines() if not line.startswith("."))

    conn = sqlite3.connect(args.db)
    for statement in filter(str.strip, script.split(";")):
        cur = conn.execute(statement)
        if cur.description is None:
            continue
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        # Überschriftszeilen der Form SELECT '— ... —' haben eine leere Spalte
        if cols == [""] and rows:
            print(f"\n{rows[0][0]}")
            continue
        if not rows:
            print("  (keine Zeilen)")
            continue
        widths = [max(len(str(c)), max(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
        print("  " + "  ".join("-" * w for w in widths))
        for r in rows:
            print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

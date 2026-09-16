from datetime import date, datetime, timedelta

from timetable import fetch as ft

SERVICE_DATE = date(2026, 9, 17)


class _Dep:
    """Minimaler Departure-Ersatz — _coverage_gap liest nur `planned`."""

    def __init__(self, planned):
        self.planned = planned


def _cadence(start: str, end: str, step_min: int = 10):
    """Fahrten im festen Takt, als HAFAS-Zeitstrings mit Tagesübertrag.

    Realistische Dichte ist für diese Tests wesentlich: die Lückenerkennung
    unterscheidet Betriebspause von Abbruch nur dann zuverlässig, wenn der
    Tagesverkehr tatsächlich dicht ist.
    """
    base = datetime.combine(SERVICE_DATE, datetime.min.time())
    cur, stop = base + _delta(start), base + _delta(end)
    out = []
    while cur <= stop:
        offset = (cur.date() - SERVICE_DATE).days
        stamp = cur.strftime("%H%M%S")
        out.append(_Dep(f"{offset:02d}{stamp}" if offset else stamp))
        cur += timedelta(minutes=step_min)
    return out


def _delta(hhmm: str) -> timedelta:
    h, m = hhmm.split(":")
    return timedelta(hours=int(h), minutes=int(m))


def test_no_gap_on_regular_service():
    assert ft._coverage_gap(_cadence("06:00", "20:00"), SERVICE_DATE) is None


def test_night_break_is_not_a_gap():
    """Ein Betriebstag läuft von morgens bis nach Mitternacht durch. Die Pause
    davor und danach liegt außerhalb von erster und letzter Fahrt und darf
    deshalb keine Warnung auslösen."""
    assert ft._coverage_gap(_cadence("04:29", "24:45"), SERVICE_DATE) is None


def test_interior_gap_is_detected():
    """Der Fall Heumarkt: die Tafel bricht nachmittags ab und läuft am Ende
    auf den Tagesanfang über — erste und letzte Zeit sehen vollständig aus,
    mitten am Tag fehlen aber Stunden."""
    board = _cadence("05:12", "17:52") + _cadence("24:01", "25:15")
    gap = ft._coverage_gap(board, SERVICE_DATE)

    assert gap is not None
    start, end, minutes = gap
    assert start == "2026-09-17 17:52:00"
    assert end == "2026-09-18 00:01:00"
    assert minutes == 369


def test_gap_below_threshold_ignored():
    """Ausdünnung am Abend auf 30-Minuten-Takt ist kein Abbruch."""
    assert ft._coverage_gap(_cadence("20:00", "23:00", step_min=30), SERVICE_DATE) is None


def test_unparsable_times_are_skipped():
    assert ft._coverage_gap([_Dep(""), _Dep(None)] + _cadence("08:00", "09:00"), SERVICE_DATE) is None


def test_reports_largest_gap_when_several():
    board = _cadence("05:00", "08:00") + _cadence("10:00", "11:00") + _cadence("15:00", "16:00")
    gap = ft._coverage_gap(board, SERVICE_DATE)

    assert gap is not None and gap[2] == 240  # 11:00 -> 15:00 schlaegt 08:00 -> 10:00

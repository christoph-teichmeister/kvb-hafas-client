"""HAFAS-Rohwerte (Zeit, Datum, Dauer) in menschenlesbare Strings."""

from kvb_hafas.parsing import _delay_minutes


def hhmm(t: str) -> str:
    """HAFAS-Zeit ("224400", ggf. mit Tages-Prefix "01224400") -> "22:44"."""
    t = t[-6:]
    return f"{t[:2]}:{t[2:4]}" if len(t) == 6 else t


def ddmm(d: str) -> str:
    """HAFAS-Datum ("20260916") -> "16.09."; alles andere unverändert."""
    return f"{d[6:8]}.{d[4:6]}." if len(d) == 8 and d.isdigit() else d


def dur_min(d: str) -> str:
    """HAFAS-Dauer ("000200") -> "2 min"; leer -> "—"."""
    if len(d) != 6 or not d.isdigit():
        return "—"
    return f"{int(d[:2]) * 60 + int(d[2:4])} min"


def delay_min(planned: str, realtime: str) -> int:
    """Verspätung in Minuten aus zwei HAFAS-Zeiten; unlesbare Zeiten -> 0."""
    return _delay_minutes(planned, realtime) or 0

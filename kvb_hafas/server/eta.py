"""Wann ist eine Fahrt an einem beliebigen Punkt? — reine Rechnung, kein Netz.

Grundlage ist der Laufweg einer Fahrt (JourneyRoute) mit Soll-/Ist-Zeiten je
Halt und der Streckenverlauf je Haltestellenpaar. Der Punkt (Standort des
Nutzers) wird auf den Verlauf projiziert; die Zeit am Lotfußpunkt wird
zwischen Abfahrt am vorigen und Ankunft am nächsten Halt linear nach
zurückgelegter Strecke interpoliert. Genau so rechnet HAFAS seine
`trainPosMode: CALC`-Positionen auch — Karte und ETA passen also zusammen,
mit derselben Ungenauigkeit (Ampeln, Haltezeiten: etwa ±1 Min.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from kvb_hafas.models import JourneyRoute, JourneyStop
from kvb_hafas.parsing import LOCAL_TZ

# Weiter weg von der Strecke gilt der Punkt als "nicht an dieser Fahrt".
MAX_ROUTE_DISTANCE_M = 300.0
# So lange nach dem Passieren zählt eine Fahrt noch als "gerade da" statt
# "vorbei" — die Position ist interpoliert, nicht gemessen.
PASSED_GRACE_SECONDS = 30.0

_EARTH_M_PER_DEG = 111_320.0

Point = tuple[float, float]


def hafas_timestamp(date: str, t: str | None) -> float | None:
    """HAFAS-Zeit ("143200", "01000300" = Folgetag) am Betriebstag `date`
    (YYYYMMDD) als Unix-Zeit, Kölner Ortszeit. None bei fehlendem/kaputtem Wert."""
    if not t or not date:
        return None
    try:
        days = int(t[:-6]) if len(t) > 6 else 0
        hh, mm, ss = int(t[-6:-4]), int(t[-4:-2]), int(t[-2:])
        base = datetime.strptime(date, "%Y%m%d")
    except ValueError:
        return None
    # Wanduhrzeit erst nach dem Tagesübertrag lokalisieren, sonst verschiebt
    # eine Fahrt über die Zeitumstellung um eine Stunde.
    wall = base + timedelta(days=days, hours=hh, minutes=mm, seconds=ss)
    return wall.replace(tzinfo=LOCAL_TZ).timestamp()


def _to_xy(p: Point, lat0: float) -> tuple[float, float]:
    """Lokale Equirektangular-Projektion in Metern — reicht auf Stadtgröße."""
    return p[1] * _EARTH_M_PER_DEG * math.cos(math.radians(lat0)), p[0] * _EARTH_M_PER_DEG


def distance_m(a: Point, b: Point) -> float:
    ax, ay = _to_xy(a, a[0])
    bx, by = _to_xy(b, a[0])
    return math.hypot(ax - bx, ay - by)


def project_on_path(point: Point, path: list[Point]) -> tuple[float, float, Point]:
    """Lotfußpunkt von `point` auf dem Polygonzug `path`.

    Liefert (Abstand in m, Anteil 0..1 der Pfadlänge bis zum Lotfußpunkt,
    Lotfußpunkt als (lat, lon)).
    """
    lat0 = point[0]
    px, py = _to_xy(point, lat0)
    xy = [_to_xy(p, lat0) for p in path]
    lengths = [math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(xy, xy[1:])]
    total = sum(lengths)
    best = (math.inf, 0.0, path[0])
    walked = 0.0
    for i, seg_len in enumerate(lengths):
        (x0, y0), (x1, y1) = xy[i], xy[i + 1]
        r = 0.0 if seg_len == 0 else max(0.0, min(1.0, ((px - x0) * (x1 - x0) + (py - y0) * (y1 - y0)) / seg_len**2))
        fx, fy = x0 + r * (x1 - x0), y0 + r * (y1 - y0)
        dist = math.hypot(px - fx, py - fy)
        if dist < best[0]:
            foot = (path[i][0] + r * (path[i + 1][0] - path[i][0]), path[i][1] + r * (path[i + 1][1] - path[i][1]))
            best = (dist, (walked + r * seg_len) / total if total else 0.0, foot)
        walked += seg_len
    return best


def _stop_point(stop: JourneyStop) -> Point | None:
    if stop.stop.lat is None or stop.stop.lon is None:
        return None
    return stop.stop.lat, stop.stop.lon


def _times(date: str, stop: JourneyStop, arrival: bool) -> tuple[float | None, float | None]:
    """(Ist oder Soll, Soll) für Ankunft bzw. Abfahrt, mit Fallback auf die
    jeweils andere — Start-/Endhalt haben nur eins von beiden."""
    if arrival:
        order = ((stop.arr_realtime, stop.arr_planned), (stop.dep_realtime, stop.dep_planned))
    else:
        order = ((stop.dep_realtime, stop.dep_planned), (stop.arr_realtime, stop.arr_planned))
    for realtime, planned in order:
        p = hafas_timestamp(date, planned)
        if p is not None:
            r = hafas_timestamp(date, realtime)
            return (r if r is not None else p), p
    return None, None


@dataclass
class Pass:
    """Eine Vorbeifahrt am Punkt, zwischen Halt `from_idx` und `from_idx + 1`."""

    from_idx: int
    distance_m: float
    point: Point
    eta: float  # Unix-Zeit, Ist wenn vorhanden
    planned: float
    realtime: bool
    cancelled: bool


def passes(route: JourneyRoute, segments: dict[tuple[str, str], list[Point]], point: Point) -> list[Pass]:
    """Alle Vorbeifahrten der Fahrt am Punkt, in Fahrtrichtung sortiert.

    Benachbarte Abschnitte innerhalb von MAX_ROUTE_DISTANCE_M gehören zu
    *einer* Vorbeifahrt (der Punkt liegt meist nahe einem Halt, also an zwei
    Abschnitten) — davon zählt der nächstgelegene. Eine Ringlinie, die
    später erneut vorbeikommt, ergibt eine zweite Vorbeifahrt.
    """
    stops = route.stops
    out: list[Pass] = []
    run: list[Pass] = []
    for i, (a, b) in enumerate(zip(stops, stops[1:])):
        pa, pb = _stop_point(a), _stop_point(b)
        hit = None
        if pa and pb:
            path = segments.get((a.stop.ext_id, b.stop.ext_id)) or [pa, pb]
            dist, frac, foot = project_on_path(point, list(path))
            t0, p0 = _times(route.date, a, arrival=False)
            t1, p1 = _times(route.date, b, arrival=True)
            if dist <= MAX_ROUTE_DISTANCE_M and None not in (t0, p0, t1, p1):
                hit = Pass(
                    from_idx=i,
                    distance_m=dist,
                    point=foot,
                    eta=t0 + frac * (t1 - t0),
                    planned=p0 + frac * (p1 - p0),
                    realtime=bool((a.dep_realtime or a.arr_realtime) and (b.arr_realtime or b.dep_realtime)),
                    cancelled=a.cancelled or b.cancelled,
                )
        if hit:
            run.append(hit)
        elif run:
            out.append(min(run, key=lambda p: p.distance_m))
            run = []
    if run:
        out.append(min(run, key=lambda p: p.distance_m))
    return out


def _nearest_stop(route: JourneyRoute, point: Point, now: float) -> dict | None:
    """Nächstgelegener noch nicht angefahrener Halt — fürs Einsteigen."""
    best = None
    for stop in route.stops:
        sp = _stop_point(stop)
        eta, planned = _times(route.date, stop, arrival=True)
        if sp is None or eta is None or eta < now - PASSED_GRACE_SECONDS:
            continue
        dist = distance_m(point, sp)
        if best is None or dist < best[0]:
            best = (dist, stop, eta, planned)
    if best is None:
        return None
    dist, stop, eta, planned = best
    return {
        "name": stop.stop.name,
        "lat": stop.stop.lat,
        "lon": stop.stop.lon,
        "distance_m": round(dist),
        "eta": eta * 1000,
        "realtime": bool(stop.arr_realtime or stop.dep_realtime),
        "delay_minutes": round((eta - planned) / 60),
        "cancelled": stop.cancelled,
    }


def journey_eta(route: JourneyRoute, segments: dict[tuple[str, str], list[Point]], point: Point, now: float) -> dict:
    """Wann ist die Fahrt am Punkt? Zeiten in ms (wie `served_at`).

    status:
      - "approaching": nächste Vorbeifahrt liegt noch vor uns
      - "passed": alle Vorbeifahrten liegen hinter uns (fährt weg)
      - "not_on_route": die Strecke kommt dem Punkt nie nahe genug
    """
    found = passes(route, segments, point)
    upcoming = [p for p in found if p.eta >= now - PASSED_GRACE_SECONDS]
    chosen = upcoming[0] if upcoming else (found[-1] if found else None)
    payload = {
        "jid": route.jid,
        "line": route.line,
        "direction": route.direction,
        "computed_at": now * 1000,
        "max_route_distance_m": MAX_ROUTE_DISTANCE_M,
        "nearest_stop": _nearest_stop(route, point, now),
    }
    if chosen is None:
        return {**payload, "status": "not_on_route"}
    return {
        **payload,
        "status": "approaching" if upcoming else "passed",
        "eta": chosen.eta * 1000,
        "realtime": chosen.realtime,
        "delay_minutes": round((chosen.eta - chosen.planned) / 60),
        "cancelled": chosen.cancelled,
        "distance_m": round(chosen.distance_m),
        "point": [round(chosen.point[0], 6), round(chosen.point[1], 6)],
        "between": [route.stops[chosen.from_idx].stop.name, route.stops[chosen.from_idx + 1].stop.name],
    }

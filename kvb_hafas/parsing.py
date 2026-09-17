"""Parser-Helfer für HAFAS-Rohantworten — kein Netzwerkzugriff."""

from __future__ import annotations

from typing import Any

from kvb_hafas.models import Line


def _line_from_prod(prod: dict[str, Any], line_id: str, op_l: list[dict[str, Any]] | None = None) -> Line:
    ctx = prod.get("prodCtx", {})
    stats = prod.get("stat", {})
    op_idx = prod.get("oprX")
    operator = None
    if op_l and op_idx is not None and op_idx < len(op_l):
        operator = op_l[op_idx].get("name")
    return Line(
        name=prod.get("name", ""),
        line_id=line_id or ctx.get("lineId", ""),
        category=ctx.get("catOut"),
        operator=operator,
        journeys=stats.get("cnt"),
        stats=stats,
    )


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google-Encoded-Polyline -> [(lat, lon), ...].

    HAFAS liefert `polyL[].crdEncYX` in genau diesem Format (`delta: true`,
    Faktor 1e5), dasselbe wie Google Maps.
    """
    points: list[tuple[float, float]] = []
    lat = lon = index = 0
    while index < len(encoded):
        for axis in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))
    return points


def station_ext_id(mast_ext_id: str) -> str | None:
    """Mast-ID (`300xxxxxNN`) -> Haltestellen-ID (`900xxxxxx`).

    JourneyDetails referenziert Halte richtungsscharf über Mast-IDs, während
    find_stops() und alle anderen Methoden die Master-Haltestelle erwarten.
    Beide hängen über die KVB-Haltestellennummer zusammen:

        Mast  = "300" + nummer(4-stellig) + steig(2-stellig)
        Halt  = 900000000 + nummer

    z.B. `300090301` -> `900000903` (Sparkasse am Butzweilerhof).

    Gibt `None` für alles, was nicht wie eine Mast-ID aussieht — unter anderem
    für Master-IDs selbst, die also nicht versehentlich umgerechnet werden.

    ponytail: Heuristik auf dem ID-Schema, kein dokumentiertes Feld. HAFAS
    liefert hier kein `mMastLocX`, mit dem man es sauber auflösen könnte.
    Verifiziert an den 34 Masten der Linie 5 (34/34). Bricht, wenn KVB die
    Mast-IDs umstellt.
    """
    if len(mast_ext_id) != 9 or not mast_ext_id.startswith("300") or not mast_ext_id.isdigit():
        return None
    return str(900_000_000 + int(mast_ext_id[3:7]))


def _mast_steig(loc_l: list[dict[str, Any]], loc_x: int | None) -> str | None:
    """Steig aus der Mast-extId ableiten, wenn HAFAS kein dPlatf liefert.

    Kleinere Haltestellen haben keine Gleisangabe, aber `stbStop.locX` zeigt auf
    den konkreten Mast in `common.locL`: die 9-stellige Haltestellen-ID
    (9000002 49) erscheint dort als 3000249 0X, letzte Ziffer = Steig.
    Der übergeordnete Eintrag (extId 900...) hat keinen Steig -> None.

    ponytail: Heuristik auf dem ID-Schema, kein dokumentiertes Feld. Bricht,
    wenn KVB die Mast-IDs umstellt — dann fällt nur der Fallback weg.
    """
    if loc_x is None or loc_x >= len(loc_l):
        return None
    ext_id = loc_l[loc_x].get("extId", "")
    if len(ext_id) == 9 and ext_id.startswith("3") and ext_id[-1] != "0":
        return f"Steig {ext_id[-1]}"
    return None


def _delay_minutes(planned: str, realtime: str) -> int | None:
    """Verspätung in Minuten aus zwei HAFAS-Zeiten ("224400", ggf. mit Tages-Prefix)."""
    if not planned or not realtime:
        return None

    def minutes(t: str) -> int:
        days = int(t[:-6] or 0) if len(t) > 6 else 0
        t = t[-6:]
        return days * 1440 + int(t[:2]) * 60 + int(t[2:4])

    try:
        return minutes(realtime) - minutes(planned)
    except ValueError:
        return None


def _seg_key(loc_l: list[dict[str, Any]], from_x: int | None, to_x: int | None) -> tuple[str, str] | None:
    """(extId, extId) der beiden Halte — Schlüssel in den Abschnitts-Geometrien."""
    if from_x is None or to_x is None or not (0 <= from_x < len(loc_l)) or not (0 <= to_x < len(loc_l)):
        return None
    return loc_l[from_x].get("extId", ""), loc_l[to_x].get("extId", "")


def _cumulative(path: list[tuple[float, float]]) -> list[float]:
    """Aufsummierte Segmentlängen (0..1), damit `proc` als Weganteil taugt.

    Luftlinie in Grad ohne Breitenkorrektur: auf Kölner Ausdehnung verzerrt das
    die Gewichtung einzelner Segmente um wenige Prozent — unsichtbar gegenüber
    der Prognose-Ungenauigkeit der Position selbst.
    """
    cum = [0.0]
    for (lat0, lon0), (lat1, lon1) in zip(path, path[1:]):
        cum.append(cum[-1] + ((lat1 - lat0) ** 2 + (lon1 - lon0) ** 2) ** 0.5)
    total = cum[-1]
    return [c / total for c in cum] if total else [0.0] * len(cum)


def _point_along(path: list[tuple[float, float]], cum: list[float], ratio: float) -> tuple[float, float]:
    """Punkt bei `ratio` (0..1) des Wegs entlang `path`."""
    ratio = min(max(ratio, 0.0), 1.0)
    for i in range(1, len(cum)):
        if ratio <= cum[i]:
            span = cum[i] - cum[i - 1]
            local = (ratio - cum[i - 1]) / span if span else 0.0
            (lat0, lon0), (lat1, lon1) = path[i - 1], path[i]
            return lat0 + (lat1 - lat0) * local, lon0 + (lon1 - lon0) * local
    return path[-1]


def _ani_track(
    ani: dict[str, Any],
    loc_l: list[dict[str, Any]],
    segments: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
) -> list[tuple[int, float, float]]:
    """`ani`-Block aus JourneyGeoPos -> [(offset_ms, lat, lon), ...].

    HAFAS liefert den Track als Fortschritt in Prozent (`proc`) zwischen zwei
    Halten (`fLocX`/`tLocX`, Indizes in `common.locL`) zu den Zeitpunkten in
    `mSec`. Daraus wird je Stützstelle eine Koordinate interpoliert, damit die
    Karte 120 Sekunden animieren kann, ohne nachzupollen.

    Die Skala ist gemessen, nicht dokumentiert: gegen die von HAFAS selbst
    mitgelieferte Animations-Polyline (`ani.polyG` -> `common.polyL`) bleiben
    mit /100 im Mittel 9 m Abweichung, mit /1000 wären es 541 m.

    `segments` (aus journey_segments()) bildet ein Haltestellenpaar auf den
    echten Streckenverlauf ab. Ist der Abschnitt bekannt, folgt der Track dem
    Verlauf inklusive der dazwischenliegenden Stützpunkte; sonst bleibt es die
    Luftlinie zwischen den beiden Halten.
    """
    m_sec = ani.get("mSec") or []
    proc, f_loc, t_loc = ani.get("proc") or [], ani.get("fLocX") or [], ani.get("tLocX") or []
    steps = list(zip(m_sec, proc, f_loc, t_loc))
    track: list[tuple[int, float, float]] = []
    for i, (offset, percent, from_x, to_x) in enumerate(steps):
        path = (segments or {}).get(_seg_key(loc_l, from_x, to_x) or ("", ""))
        ratio = percent / 100
        if path and len(path) > 1:
            cum = _cumulative(path)
            track.append((offset, *_point_along(path, cum, ratio)))
            # Stützpunkte des Streckenverlaufs bis zum nächsten ani-Schritt
            # mitgeben — sonst würde der Browser die Kurve wieder abschneiden.
            if i + 1 < len(steps):
                next_offset, next_percent, next_from, next_to = steps[i + 1]
                next_ratio = next_percent / 100
                if (next_from, next_to) == (from_x, to_x) and next_ratio > ratio:
                    for j, c in enumerate(cum):
                        if ratio < c < next_ratio:
                            share = (c - ratio) / (next_ratio - ratio)
                            track.append((round(offset + (next_offset - offset) * share), *path[j]))
            continue
        from_crd, to_crd = _crd(loc_l, from_x), _crd(loc_l, to_x)
        if from_crd is None or to_crd is None:
            continue
        track.append(
            (
                offset,
                from_crd[0] + (to_crd[0] - from_crd[0]) * ratio,
                from_crd[1] + (to_crd[1] - from_crd[1]) * ratio,
            )
        )
    return track


def _crd(loc_l: list[dict[str, Any]], loc_x: int | None) -> tuple[float, float] | None:
    """`common.locL[loc_x].crd` -> (lat, lon); None, wenn der Index nicht passt."""
    if loc_x is None or loc_x < 0 or loc_x >= len(loc_l):
        return None
    crd = loc_l[loc_x].get("crd") or {}
    if "x" not in crd or "y" not in crd:
        return None
    return crd["y"] / 1_000_000, crd["x"] / 1_000_000


def _hex_colour(rgb: dict[str, Any] | None) -> str:
    """HAFAS-Farbe (`{"r":…, "g":…, "b":…}`) -> "#rrggbb"; None -> ""."""
    if not rgb:
        return ""
    return "#{:02x}{:02x}{:02x}".format(rgb.get("r", 0), rgb.get("g", 0), rgb.get("b", 0))


def _prod_colours(prod: dict[str, Any], ico_l: list[dict[str, Any]]) -> tuple[str, str]:
    """Hintergrund- und Schriftfarbe einer Linie aus `common.icoL`.

    HAFAS hält die offiziellen Linienfarben selbst vor — `prodL[].icoX` zeigt
    auf einen icoL-Eintrag mit `bg`/`fg`. Für die KVB-Stadtbahn sind das genau
    die Farben aus dem Liniennetzplan (1 rot, 15 grün, 18 blau …), also braucht
    es dafür keine eigene Tabelle.
    """
    idx = prod.get("icoX")
    ico = ico_l[idx] if idx is not None and 0 <= idx < len(ico_l) else {}
    return _hex_colour(ico.get("bg")), _hex_colour(ico.get("fg"))


def _prod_name(prod: dict[str, Any]) -> str:
    """Anzeigename einer Linie aus einem `prodL`-Eintrag.

    `name` fehlt bei manchen DB-Produkten im Radar (Fern- und Regionalzüge);
    dort steht die Linie nur in `nameS` oder in `prodCtx` (`line`/`num`). Ohne
    diesen Fallback bleibt der Marker auf der Karte ohne Label.
    """
    ctx = prod.get("prodCtx", {})
    return prod.get("name") or prod.get("nameS") or ctx.get("line") or ctx.get("num") or ""

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



"""Datenmodelle des KVB-HAFAS-Clients — reine Container ohne Request-Logik."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class KVBHafasError(Exception):
    """Raised when the HAFAS gate returns a non-OK error code."""


@dataclass
class Stop:
    name: str
    ext_id: str
    lat: float | None = None
    lon: float | None = None
    lines: tuple[str, ...] = ()  # only filled by nearby_stops(with_lines=True)


@dataclass
class Departure:
    line: str
    direction: str
    planned: str  # HHMMSS-ish HAFAS time string, e.g. "224400"
    realtime: str | None
    platform: str | None
    cancelled: bool
    jid: str  # pass to client.journey_details() for the full stop sequence


@dataclass
class ServiceAlert:
    text: str
    category: int  # cat: 1=Aufzug/Fahrzeuge, 2/3=Baumaßnahme/Verlegung, 99=Marketing (unverifizierte Zuordnung)
    priority: int
    valid_from: str  # sDate (YYYYMMDD)
    valid_to: str  # eDate (YYYYMMDD)
    head: str = ""  # Kurzfassung, oft leer
    hid: str = ""  # HIM-ID, stabil über mehrere Abfragen
    stops: tuple[str, ...] = ()  # betroffene Haltestellen laut Loc-Referenz
    # Koordinaten der betroffenen Orte (aus himMsgEdgeL.icoCrd bzw. den Loc-Referenzen)
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Leg:
    """Ein Abschnitt einer Verbindung: entweder eine Fahrt oder ein Fußweg."""

    walk: bool
    from_name: str
    to_name: str
    dep_time: str
    arr_time: str
    line: str | None = None  # None bei Fußweg
    direction: str | None = None
    dep_platform: str | None = None
    arr_platform: str | None = None
    dist_m: int | None = None  # nur bei Fußweg
    gis_ctx: str = ""  # nur bei Fußweg: Token für walk_route(), siehe docs/API.md#gisroute


@dataclass
class Connection:
    dep_time: str
    arr_time: str
    num_changes: int  # Anzahl Fahrt-Abschnitte - 1 (Fußwege zählen nicht)
    legs: list[Leg] = field(default_factory=list)
    ctx_recon: str = ""  # Token für reconstruct(), siehe docs/API.md#reconstruction


@dataclass
class WalkRoute:
    """Straßengenauer Fußweg zu einem Fußweg-Abschnitt (GisRoute)."""

    dist_m: int
    duration: str  # HAFAS-Dauer "HHMMSS"
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class ServerInfo:
    """Fahrplanperiode und Serverzeit (ServerInfo)."""

    timetable_from: str  # fpB (YYYYMMDD) — frühestes abfragbares Datum
    timetable_to: str  # fpE
    date: str  # Serverdatum
    time: str  # Serverzeit


@dataclass
class Line:
    name: str  # Label wie auf dem Abfahrtsmonitor ("18", "146")
    line_id: str  # z.B. "de:vrs:18" — Eingabe für line_details()
    category: str | None = None  # catOut: "Str" (Stadtbahn), "Bus", ...
    operator: str | None = None
    journeys: int | None = None  # stat.cnt: Fahrten im Fahrplan
    stats: dict[str, Any] = field(default_factory=dict)  # roher stat-Block, siehe line_details()


@dataclass
class Vehicle:
    """Live-Position eines Fahrzeugs (JourneyGeoPos)."""

    line: str
    direction: str
    lat: float
    lon: float
    jid: str
    category: str = ""  # prodCtx.catOut: "Str" (Stadtbahn), "Bus", "S-Bahn", ...
    bearing: int | None = None  # dirGeo: Fahrtrichtung in Grad
    delay: int | None = None  # Minuten am nächsten Halt, None = keine Echtzeit
    next_stop: str = ""
    colour: str = ""  # offizielle Linienfarbe aus common.icoL, "#rrggbb"
    text_colour: str = ""  # passende Schriftfarbe dazu
    # Animations-Track (offset_ms, lat, lon) aus dem ani-Block, 0..120 s ab
    # Abfragezeitpunkt — damit eine Karte flüssig animieren kann, ohne zu pollen.
    track: list[tuple[int, float, float]] = field(default_factory=list)


@dataclass
class Reachable:
    stop: Stop
    minutes: int  # Reisezeit ab Startpunkt
    changes: int


@dataclass
class JourneyStop:
    """Ein Halt im Laufweg einer Fahrt (JourneyDetails)."""

    idx: int  # Position im Laufweg, 0 = Startpunkt
    stop: Stop  # ext_id ist die Mast-ID (300xxxxxNN), nicht die Haltestellen-ID
    arr_planned: str | None  # HAFAS-Zeitstring, ggf. mit Tagesübertrags-Präfix
    dep_planned: str | None
    arr_realtime: str | None = None
    dep_realtime: str | None = None
    cancelled: bool = False
    station_ext_id: str | None = None  # Master-Haltestelle, beide Richtungen gemeinsam


@dataclass
class JourneyRoute:
    """Kompletter Laufweg einer Fahrt, Halte bereits aufgelöst."""

    jid: str
    line: str
    direction: str
    date: str  # YYYYMMDD, Betriebstag der Fahrt
    service_days: str  # sDaysI, Klartext ("16. bis 30. Sep 2026 Mo - Fr")
    service_bits: str  # sDaysB, Bitmaske ab fpB (siehe server_info())
    stops: list[JourneyStop] = field(default_factory=list)


@dataclass
class ScheduledJourney:
    """Eine Fahrt aus dem Fahrplan (JourneyMatch) — ohne Echtzeit."""

    line: str
    from_name: str
    to_name: str
    dep_time: str
    arr_time: str
    jid: str
    service_days: str  # Klartext, z.B. "Mo - Fr; nicht 10. bis 28. Aug"

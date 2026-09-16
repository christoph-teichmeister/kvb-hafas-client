"""
Inoffizieller HAFAS-Client für die KVB (Kölner Verkehrs-Betriebe).

Reverse-engineered aus dem öffentlich zugänglichen Widget-Generator unter
https://auskunft.kvb.koeln/widgetgenerator.html - siehe README.md für Details
und rechtliche Hinweise.

WICHTIG: Nicht offiziell von der KVB freigegeben. Auf eigenes Risiko nutzen,
nicht kommerziell einsetzen, Requests rate-limiten, KVB-Branding nicht
verwenden.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import requests

ENDPOINT = "https://auskunft.kvb.koeln/gate"
# Public HAFAS access ID, served in cleartext by the KVB widget generator at
# https://auskunft.kvb.koeln/widgetgenerator.html. Shared by every widget user,
# not a per-user secret - no checksum or salt required. Secret scanners may flag
# it; that is a false positive. KVB can rotate or block it at any time.
AID = "Rt6foY5zcTTRXMQs"
USER_AGENT = "Mozilla/5.0 (compatible; kvb-hafas-client/0.1)"


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


class KVBHafasClient:
    """Minimal client for the KVB HAFAS mgate endpoint.

    Example:
        client = KVBHafasClient()
        stops = client.find_stops("Neumarkt")
        deps = client.station_board(stops[0].ext_id)
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        min_interval: float = 0.0,
    ):
        """`min_interval` erzwingt einen Mindestabstand (Sekunden) zwischen zwei
        Requests. Default 0 = aus, damit interaktive Nutzung nicht ausgebremst
        wird; Batch-Jobs (siehe fetch_timetable.py) setzen es auf >= 1.0, um die
        Rate-Limit-Zusage aus dem README einzuhalten."""
        self.session = session or requests.Session()
        self.timeout = timeout
        self.min_interval = min_interval
        self._req_counter = 0
        self._last_call = 0.0

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"{self._req_counter}@"

    def _call(self, meth: str, req: dict[str, Any]) -> dict[str, Any]:
        if self.min_interval:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        payload = {
            "id": self._next_id(),
            "ver": "1.16",
            "lang": "deu",
            "auth": {"type": "AID", "aid": AID},
            "client": {"id": "HAFAS", "type": "WEB"},
            "svcReqL": [{"meth": meth, "req": req}],
        }
        resp = self.session.post(
            ENDPOINT,
            json=payload,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("err") and data["err"] != "OK":
            # Top-level envelope error (malformed request) — no svcResL entries at all.
            raise KVBHafasError(f"{meth} failed at envelope level: {data['err']} ({data.get('errTxt', '')})")
        svc_res = data["svcResL"][0]
        if svc_res.get("err") != "OK":
            raise KVBHafasError(f"{meth} failed: {svc_res.get('err')}")
        return svc_res["res"]

    def find_stops(self, query: str, max_results: int = 5) -> list[Stop]:
        """Search for stops by (partial) name. Returns best matches first."""
        res = self._call(
            "LocMatch",
            {
                "input": {
                    "field": "S",
                    "loc": {"name": f"{query}?", "type": "S"},
                    "maxLoc": max_results,
                }
            },
        )
        loc_l = res.get("match", {}).get("locL", [])
        stops = []
        for loc in loc_l:
            crd = loc.get("crd")
            stops.append(
                Stop(
                    name=loc.get("name", ""),
                    ext_id=loc.get("extId", ""),
                    lat=crd["y"] / 1_000_000 if crd else None,
                    lon=crd["x"] / 1_000_000 if crd else None,
                )
            )
        return stops

    def nearby_stops(
        self,
        lat: float,
        lon: float,
        max_dist_m: int = 500,
        max_results: int = 10,
        with_lines: bool = False,
    ) -> list[Stop]:
        """Find stops within max_dist_m meters of the given coordinates.

        POIs (museums, landmarks, ...) are excluded via `getPOIs: False` —
        they carry an extId that StationBoard answers with a region-wide
        board, which is useless here.

        `with_lines`: also fill Stop.lines with the line labels currently
        serving each stop. LocGeoPos itself carries no per-stop product
        info, so this costs one extra StationBoard request per stop and
        only sees lines with an upcoming departure.
        """
        res = self._call(
            "LocGeoPos",
            {
                "ring": {
                    "cCrd": {"x": round(lon * 1_000_000), "y": round(lat * 1_000_000)},
                    "maxDist": max_dist_m,
                },
                "maxLoc": max_results,
                "getPOIs": False,
            },
        )
        stops = []
        for loc in res.get("locL", []):
            crd = loc.get("crd")
            stops.append(
                Stop(
                    name=loc.get("name", ""),
                    ext_id=loc.get("extId", ""),
                    lat=crd["y"] / 1_000_000 if crd else None,
                    lon=crd["x"] / 1_000_000 if crd else None,
                )
            )
        if with_lines:
            stops = [replace(stop, lines=self.stop_lines(stop.ext_id)) for stop in stops]
        return stops

    def stop_lines(self, stop_ext_id: str) -> tuple[str, ...]:
        """Line labels serving this stop, sorted — from the timetable, not the board."""
        return self.stop_details(stop_ext_id).lines

    def journey_details(self, jid: str) -> dict[str, Any]:
        """Fetch full stop-by-stop details for a single journey.

        `jid` comes from `Departure.jid`.

        Returns the raw `journey` dict (includes `stopL`: every stop with
        planned/realtime arrival+departure times and platform info). Die
        Halte darin referenzieren Orte nur per `locX`-Index — zum Auflösen
        siehe journey_route().
        """
        res = self._call("JourneyDetails", {"jid": jid})
        return res.get("journey", {})

    def journey_route(self, jid: str) -> JourneyRoute:
        """Kompletter Laufweg einer Fahrt mit aufgelösten Haltestellen.

        Im Gegensatz zu journey_details() wird hier `common.locL`
        mitausgewertet — ohne das sind die `locX`-Indizes der Halte
        wertlos. Liefert außerdem die Verkehrstage (`sDaysL`), die sonst nur
        JourneyMatch herausgibt.

        Achtung: die `ext_id` eines Halts ist eine **Mast-ID** (9-stellig,
        `300xxxxxNN`), nicht die Haltestellen-ID aus find_stops()
        (`900xxxxxx`). Pro Richtung/Bahnsteig eine eigene ID — fürs
        Zusammenfassen beider Richtungen über `name` gruppieren.
        """
        res = self._call("JourneyDetails", {"jid": jid})
        jny = res.get("journey", {})
        loc_l = res.get("common", {}).get("locL", [])
        prod_l = res.get("common", {}).get("prodL", [])
        prod_idx = jny.get("prodX")
        s_days = (jny.get("sDaysL") or [{}])[0]

        stops = []
        for st in jny.get("stopL", []):
            loc = loc_l[st["locX"]] if st.get("locX") is not None and st["locX"] < len(loc_l) else {}
            crd = loc.get("crd", {})
            stops.append(
                JourneyStop(
                    idx=st.get("idx", 0),
                    stop=Stop(
                        name=loc.get("name", ""),
                        ext_id=loc.get("extId", ""),
                        lat=crd.get("y", 0) / 1_000_000 if crd.get("y") is not None else None,
                        lon=crd.get("x", 0) / 1_000_000 if crd.get("x") is not None else None,
                    ),
                    arr_planned=st.get("aTimeS"),
                    dep_planned=st.get("dTimeS"),
                    arr_realtime=st.get("aTimeR"),
                    dep_realtime=st.get("dTimeR"),
                    cancelled=bool(st.get("aCncl") or st.get("dCncl")),
                )
            )

        return JourneyRoute(
            jid=jny.get("jid", jid),
            line=prod_l[prod_idx].get("name", "") if prod_idx is not None and prod_idx < len(prod_l) else "",
            direction=jny.get("dirTxt", ""),
            date=jny.get("date", ""),
            service_days=s_days.get("sDaysI", ""),
            service_bits=s_days.get("sDaysB", ""),
            stops=stops,
        )

    def station_board(
        self,
        stop_ext_id: str,
        max_journeys: int = 10,
        date: str | None = None,
        time: str | None = None,
        board_type: str = "DEP",
    ) -> list[Departure]:
        """Fetch the departure board for a stop (by extId, see find_stops).

        Ohne `date`/`time` (Format `YYYYMMDD` / `HHMMSS`) gilt "jetzt" und die
        Antwort trägt Echtzeitwerte. Mit einem Datum lässt sich der Fahrplan
        innerhalb der Fahrplanperiode (siehe server_info()) abfragen — dann
        liefert HAFAS allerdings nur Soll-Zeiten, `realtime` bleibt leer.

        `board_type` ist `"DEP"` (Abfahrten) oder `"ARR"` (Ankünfte). An einer
        Endhaltestelle deckt DEP die eine, ARR die andere Fahrtrichtung ab.

        `max_journeys` wird serverseitig durch die Dichte der Haltestelle
        gedeckelt: an einem ruhigen Halt reicht ein Request für den ganzen
        Tag, an einem Knoten wie Heumarkt bricht die Liste vorzeitig ab —
        dort muss über `time` weitergeblättert werden.
        """
        req: dict[str, Any] = {
            "type": board_type,
            "stbLoc": {"extId": stop_ext_id},
            "maxJny": max_journeys,
        }
        if date:
            req["date"] = date
        if time:
            req["time"] = time
        res = self._call("StationBoard", req)
        prod_l = res.get("common", {}).get("prodL", [])
        loc_l = res.get("common", {}).get("locL", [])
        departures = []
        for jny in res.get("jnyL", []):
            stb = jny.get("stbStop", {})
            prod_idx = jny.get("prodX")
            line = prod_l[prod_idx]["name"] if prod_idx is not None and prod_idx < len(prod_l) else "?"
            departures.append(
                Departure(
                    line=line,
                    direction=jny.get("dirTxt", ""),
                    planned=stb.get("dTimeS") or stb.get("aTimeS", ""),
                    realtime=stb.get("dTimeR") or stb.get("aTimeR"),
                    platform=stb.get("dPlatfR") or stb.get("dPlatfS") or _mast_steig(loc_l, stb.get("locX")),
                    cancelled=bool(jny.get("isCncl", False)),
                    jid=jny.get("jid", ""),
                )
            )
        return departures

    def service_alerts(self, stop: Stop | None = None, line: str | None = None) -> list[ServiceAlert]:
        """Fetch currently active service alerts, optionally narrowed down.

        Without arguments: all active alerts network-wide — construction
        notices, elevator outages, stop relocations, and mixed in KVB
        marketing announcements (cat=99 in the raw data; filter those out
        client-side if you only want disruptions).

        `line`: line label as shown on the station board ("133", "18").
        Filtered server-side (`himFltrL` type LINE). An unknown label
        returns an empty list rather than an error.

        `stop` (a Stop from find_stops/nearby_stops): filtered client-side,
        since HAFAS has no working server-side station filter (see
        docs/API.md). An alert matches if it references the stop's extId in
        the response's locL, or names the stop in its text (most alerts
        carry no loc reference at all and only name the stop in plain text,
        e.g. "(H) Ulrepforte").
        """
        him_fltr = [{"type": "LINE", "mode": "INC", "value": line}] if line else []
        res = self._call("HimSearch", {"himFltrL": him_fltr})
        common = res.get("common", {})
        alerts = []
        for msg in res.get("msgL", []):
            if stop is not None and not self._alert_matches_stop(msg, common, stop):
                continue
            alerts.append(
                ServiceAlert(
                    text=msg.get("text", "").strip(),
                    category=msg.get("cat", -1),
                    priority=msg.get("prio", -1),
                    valid_from=msg.get("sDate", ""),
                    valid_to=msg.get("eDate", ""),
                )
            )
        return alerts

    @staticmethod
    def _alert_matches_stop(msg: dict[str, Any], common: dict[str, Any], stop: Stop) -> bool:
        loc_l = common.get("locL", [])
        event_l = common.get("himMsgEventL", [])

        loc_idx = {msg.get("fLocX"), msg.get("tLocX")}
        for ref in msg.get("eventRefL", []):
            if ref < len(event_l):
                loc_idx |= {event_l[ref].get("fLocX"), event_l[ref].get("tLocX")}
        for idx in loc_idx:
            if idx is None or idx >= len(loc_l):
                continue
            loc = loc_l[idx]
            # Stop entries come as a platform-level loc (extId 300xxxxxx) plus a
            # master loc (extId 900xxxxxx) — find_stops returns the latter.
            candidates = [loc]
            mast = loc.get("mMastLocX")
            if mast is not None and mast < len(loc_l):
                candidates.append(loc_l[mast])
            if any(c.get("extId") == stop.ext_id for c in candidates):
                return True

        # Naive name match: drop leading city/district words off "Köln Lindenthal
        # Bachemer Str." until something matches the text's "(H) Bachemer Str.".
        # Remainders that are short single words ("Str.", "Bf") are skipped —
        # they'd match nearly every alert.
        words = stop.name.split()
        text = msg.get("text", "")
        for i in range(len(words)):
            name = " ".join(words[i:])
            if (len(words) - i >= 2 or len(name) >= 6) and name in text:
                return True
        return False

    def trip_search(
        self, from_ext_id: str, to_ext_id: str, date: str | None = None, time: str | None = None
    ) -> list[Connection]:
        """Search for connections between two stops (by extId, see find_stops).

        `date`/`time` optional (YYYYMMDD / HHMMSS) — defaults to "now" if
        omitted. Subject to the same timetable-period limits as
        station_board(), see docs/API.md#historische-daten.
        """
        req: dict[str, Any] = {
            "depLocL": [{"extId": from_ext_id}],
            "arrLocL": [{"extId": to_ext_id}],
        }
        if date:
            req["outDate"] = date
        if time:
            req["outTime"] = time

        return self._parse_connections(self._call("TripSearch", req))

    def _parse_connections(self, res: dict[str, Any]) -> list[Connection]:
        """`outConL` einer TripSearch-/Reconstruction-Antwort in Connections übersetzen."""
        common = res.get("common", {})
        loc_l = common.get("locL", [])
        prod_l = common.get("prodL", [])

        def loc_name(idx: int | None) -> str:
            return loc_l[idx].get("name", "") if idx is not None and idx < len(loc_l) else ""

        connections = []
        for con in res.get("outConL", []):
            legs = []
            for sec in con.get("secL", []):
                dep, arr = sec.get("dep", {}), sec.get("arr", {})
                jny = sec.get("jny")
                prod_idx = (jny or {}).get("prodX")
                legs.append(
                    Leg(
                        walk=jny is None,
                        from_name=loc_name(dep.get("locX")),
                        to_name=loc_name(arr.get("locX")),
                        dep_time=dep.get("dTimeR") or dep.get("dTimeS", ""),
                        arr_time=arr.get("aTimeR") or arr.get("aTimeS", ""),
                        line=prod_l[prod_idx].get("name") if prod_idx is not None and prod_idx < len(prod_l) else None,
                        direction=(jny or {}).get("dirTxt"),
                        dep_platform=dep.get("dPlatfR") or dep.get("dPlatfS"),
                        arr_platform=arr.get("aPlatfR") or arr.get("aPlatfS"),
                        dist_m=sec.get("gis", {}).get("dist") if jny is None else None,
                        gis_ctx=sec.get("gis", {}).get("ctx", "") if jny is None else "",
                    )
                )
            # Umstiege = Fahrten - 1. secL enthält auch Fußwege (oft mehrere
            # winzige zwischen den Steigen derselben Haltestelle), die sonst
            # als Umstieg durchgehen würden.
            rides = sum(1 for leg in legs if not leg.walk)
            connections.append(
                Connection(
                    dep_time=con.get("dep", {}).get("dTimeS", ""),
                    arr_time=con.get("arr", {}).get("aTimeS", ""),
                    num_changes=max(rides - 1, 0),
                    legs=legs,
                    ctx_recon=con.get("ctxRecon", ""),
                )
            )
        return connections

    # --- weitere HAFAS-Methoden (siehe docs/API.md) -------------------------

    def server_info(self) -> ServerInfo:
        """Fahrplanperiode und Serverzeit abfragen (ServerInfo).

        `timetable_from`/`timetable_to` sind die harten Grenzen für alle
        Datumsangaben — außerhalb antwortet HAFAS mit H9360.
        """
        res = self._call("ServerInfo", {})
        return ServerInfo(
            timetable_from=res.get("fpB", ""),
            timetable_to=res.get("fpE", ""),
            date=res.get("sD", ""),
            time=res.get("sT", ""),
        )

    def stop_details(self, stop_ext_id: str) -> Stop:
        """Haltestellen-Detail inkl. aller bedienenden Linien (LocDetails).

        Anders als stop_lines() über die Abfahrtstafel sind das die Linien
        laut Fahrplan, unabhängig davon, ob gerade eine Fahrt ansteht.
        """
        res = self._call("LocDetails", {"locL": [{"type": "S", "lid": f"A=1@L={stop_ext_id}@"}]})
        loc_l = res.get("locL", [])
        if not loc_l:
            raise KVBHafasError(f"LocDetails returned no location for {stop_ext_id}")
        loc = loc_l[0]
        prod_l = res.get("common", {}).get("prodL", [])
        lines = [prod_l[i].get("name", "") for i in loc.get("pRefL", []) if i < len(prod_l)]
        crd = loc.get("crd")
        return Stop(
            name=loc.get("name", ""),
            ext_id=loc.get("extId", ""),
            lat=crd["y"] / 1_000_000 if crd else None,
            lon=crd["x"] / 1_000_000 if crd else None,
            lines=tuple(sorted(set(lines) - {""}, key=lambda name: (len(name), name))),
        )

    def reachable_stops(
        self,
        stop_ext_id: str,
        max_minutes: int = 15,
        max_changes: int = 0,
        date: str | None = None,
        time: str | None = None,
    ) -> list[Reachable]:
        """Isochrone: alle Haltestellen, die in max_minutes erreichbar sind (LocGeoReach).

        Der Startpunkt selbst ist als erster Eintrag mit minutes=0 enthalten.
        `max_changes=0` heißt: nur Direktverbindungen.
        """
        req: dict[str, Any] = {
            "loc": {"type": "S", "lid": f"A=1@L={stop_ext_id}@"},
            "maxDur": max_minutes,
            "maxChg": max_changes,
        }
        if date:
            req["date"] = date
        if time:
            req["time"] = time
        res = self._call("LocGeoReach", req)
        loc_l = res.get("common", {}).get("locL", [])
        # posL zeigt auf einzelne Steige; pro Haltestelle nur den schnellsten
        # behalten und auf den Master-Eintrag (extId 900xxxxxx) auflösen.
        best: dict[str, Reachable] = {}
        for pos in res.get("posL", []):
            idx = pos.get("locX")
            if idx is None or idx >= len(loc_l):
                continue
            loc = loc_l[idx]
            mast = loc.get("mMastLocX")
            if mast is not None and mast < len(loc_l):
                loc = loc_l[mast]
            crd = loc.get("crd")
            ext_id = loc.get("extId", "")
            entry = Reachable(
                stop=Stop(
                    name=loc.get("name", ""),
                    ext_id=ext_id,
                    lat=crd["y"] / 1_000_000 if crd else None,
                    lon=crd["x"] / 1_000_000 if crd else None,
                ),
                minutes=pos.get("dur", 0),
                changes=pos.get("chg", 0),
            )
            if ext_id not in best or entry.minutes < best[ext_id].minutes:
                best[ext_id] = entry
        return sorted(best.values(), key=lambda r: (r.minutes, r.stop.name))

    def vehicle_positions(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        max_vehicles: int = 100,
    ) -> list[Vehicle]:
        """Live-Positionen aller Fahrzeuge in einer Bounding-Box (JourneyGeoPos).

        Positionen sind interpoliert (`trainPosMode: CALC`) — HAFAS rechnet
        sie aus Fahrplan plus Echtzeit-Prognose hoch, es sind keine
        GPS-Rohdaten. Die Box umfasst auch Regionalzüge im Bediengebiet.
        """
        res = self._call(
            "JourneyGeoPos",
            {
                "maxJny": max_vehicles,
                "onlyRT": False,
                "rect": {
                    "llCrd": {"x": round(min_lon * 1_000_000), "y": round(min_lat * 1_000_000)},
                    "urCrd": {"x": round(max_lon * 1_000_000), "y": round(max_lat * 1_000_000)},
                },
                "perSize": 120000,
                "perStep": 30000,
                "ageOfReport": True,
                "trainPosMode": "CALC",
            },
        )
        prod_l = res.get("common", {}).get("prodL", [])
        vehicles = []
        for jny in res.get("jnyL", []):
            pos = jny.get("pos")
            if not pos:
                continue
            prod_idx = jny.get("prodX")
            vehicles.append(
                Vehicle(
                    line=prod_l[prod_idx].get("name", "") if prod_idx is not None and prod_idx < len(prod_l) else "",
                    direction=jny.get("dirTxt", ""),
                    lat=pos["y"] / 1_000_000,
                    lon=pos["x"] / 1_000_000,
                    jid=jny.get("jid", ""),
                )
            )
        return vehicles

    def find_lines(self, query: str) -> list[Line]:
        """Linien nach Label suchen (LineMatch).

        Achtung: Der Datenbestand reicht über die KVB hinaus — "1" trifft
        auch Linien aus VRR, Aachen und den Niederlanden. Die `line_id`
        (`de:vrs:...` für Köln/Bonn) zeigt, wer gemeint ist.
        """
        res = self._call("LineMatch", {"input": query})
        prod_l = res.get("common", {}).get("prodL", [])
        return [_line_from_prod(prod_l[ln["prodX"]], ln.get("lineId", "")) for ln in res.get("lineL", []) if ln.get("prodX", -1) < len(prod_l)]

    def line_details(self, line_id: str) -> Line:
        """Details zu einer Linie (LineDetails), inkl. Pünktlichkeits-Statistik.

        `line_id` im Format aus find_lines() ("de:vrs:18") — das reine Label
        ("18") liefert FAIL.

        Der `stats`-Block hat die richtigen Felder (`cnt` = Fahrten,
        `cncl` = Ausfälle, `ont` = pünktlich, `delCntL` = Verspätungs-
        Histogramm zu den Minuten-Grenzen in `delGrpL`), aber bei der KVB
        ist außer `cnt` bisher alles 0 — offenbar nicht befüllt.
        """
        res = self._call("LineDetails", {"lineId": line_id})
        prod_l = res.get("common", {}).get("prodL", [])
        if not prod_l:
            raise KVBHafasError(f"LineDetails returned no product for {line_id}")
        return _line_from_prod(prod_l[0], line_id, res.get("common", {}).get("opL", []))

    def find_journeys(self, query: str, date: str | None = None, time: str | None = None) -> list[ScheduledJourney]:
        """Fahrten nach Linien-Label suchen (JourneyMatch).

        Liefert den Fahrplan, keine Echtzeit — dafür `service_days` im
        Klartext ("Mo - Fr; nicht 10. bis 28. Aug") und eine `jid` für
        journey_details(). `date`/`time` sind serverseitig Pflicht und
        werden sonst mit "jetzt" gefüllt.
        """
        now = datetime.now()
        req: dict[str, Any] = {
            "input": query,
            # Beide Felder sind Pflicht — ohne date/time antwortet JourneyMatch mit FAIL.
            "date": date or now.strftime("%Y%m%d"),
            "time": time or now.strftime("%H%M%S"),
        }
        res = self._call("JourneyMatch", req)
        common = res.get("common", {})
        loc_l, prod_l = common.get("locL", []), common.get("prodL", [])

        def loc_name(idx: int | None) -> str:
            return loc_l[idx].get("name", "") if idx is not None and idx < len(loc_l) else ""

        journeys = []
        for jny in res.get("jnyL", []):
            stop_l = jny.get("stopL", [])
            first, last = (stop_l[0], stop_l[-1]) if stop_l else ({}, {})
            prod_idx = jny.get("prodX")
            s_days = jny.get("sDaysL") or [{}]
            journeys.append(
                ScheduledJourney(
                    line=prod_l[prod_idx].get("name", "") if prod_idx is not None and prod_idx < len(prod_l) else "",
                    from_name=loc_name(first.get("locX")),
                    to_name=loc_name(last.get("locX")),
                    dep_time=first.get("dTimeS", ""),
                    arr_time=last.get("aTimeS", ""),
                    jid=jny.get("jid", ""),
                    service_days=s_days[0].get("sDaysI", ""),
                )
            )
        return journeys

    def journey_course(self, jid: str) -> list[tuple[float, float]]:
        """Linienverlauf einer Fahrt als (lat, lon)-Punkte (JourneyCourse).

        Standardmäßig ein Punkt pro Halt — genug, um die Fahrt auf einer
        Karte zu zeichnen, aber keine straßengenaue Geometrie.
        """
        res = self._call("JourneyCourse", {"jid": jid})
        poly_l = res.get("common", {}).get("polyL", [])
        return _decode_polyline(poly_l[0].get("crdEncYX", "")) if poly_l else []

    def alerts_in_area(
        self, min_lat: float, min_lon: float, max_lat: float, max_lon: float
    ) -> list[ServiceAlert]:
        """Störungsmeldungen in einer Bounding-Box (HimGeoPos).

        Der geografische Gegenentwurf zum fehlenden Haltestellen-Filter von
        HimSearch. Nur Meldungen mit Geo-Bezug tauchen hier auf — die vielen
        Meldungen, die ihre Haltestelle nur im Text nennen, fehlen. Für Köln
        kam die Liste bisher leer zurück; service_alerts(stop=...) bleibt
        der verlässlichere Weg.
        """
        res = self._call(
            "HimGeoPos",
            {
                "rect": {
                    "llCrd": {"x": round(min_lon * 1_000_000), "y": round(min_lat * 1_000_000)},
                    "urCrd": {"x": round(max_lon * 1_000_000), "y": round(max_lat * 1_000_000)},
                }
            },
        )
        return [
            ServiceAlert(
                text=msg.get("text", "").strip(),
                category=msg.get("cat", -1),
                priority=msg.get("prio", -1),
                valid_from=msg.get("sDate", ""),
                valid_to=msg.get("eDate", ""),
            )
            for msg in res.get("msgL", [])
        ]

    def reconstruct(self, ctx_recon: str) -> list[Connection]:
        """Eine früher gefundene Verbindung neu auflösen (Reconstruction).

        `ctx_recon` stammt aus Connection.ctx_recon einer trip_search().
        Nützlich, um eine gemerkte Verbindung später mit frischen
        Echtzeitdaten abzufragen, ohne erneut zu suchen.
        """
        return self._parse_connections(self._call("Reconstruction", {"ctxRecon": ctx_recon}))

    def walk_route(self, gis_ctx: str) -> WalkRoute:
        """Straßengenauen Verlauf eines Fußweg-Abschnitts holen (GisRoute).

        `gis_ctx` kommt aus Leg.gis_ctx eines Fußwegs einer trip_search().
        Selbst gebaute Kontexte lehnt der Server ab (FAIL) — es geht nur mit
        einem Token, das er vorher selbst ausgegeben hat. Damit ist das hier
        kein freies A-nach-B-Routing, sondern die Lupe auf einen Fußweg,
        den HAFAS ohnehin schon vorgeschlagen hat.
        """
        res = self._call("GisRoute", {"gisCtx": gis_ctx, "getPolyline": True})
        con_l = res.get("conL", [])
        if not con_l:
            return WalkRoute(dist_m=0, duration="", points=[])
        sec_l = con_l[0].get("secL", [{}])
        gis = sec_l[0].get("gis", {})
        poly_l = res.get("common", {}).get("polyL", [])
        return WalkRoute(
            dist_m=gis.get("dist", 0),
            duration=con_l[0].get("dur", ""),
            points=_decode_polyline(poly_l[0].get("crdEncYX", "")) if poly_l else [],
        )

    def trip_alternatives(self, ctx_recon: str) -> list[Connection]:
        """Alternativen zu einer Verbindung suchen (SearchOnTrip).

        `ctx_recon` kommt aus Connection.ctx_recon einer trip_search().
        Anders als reconstruct() (genau dieselbe Verbindung, frische
        Echtzeit) liefert das hier die Verbindung **plus spätere
        Alternativen** auf derselben Relation — im Test 12 Stück.

        `date`/`time` werden von dieser Methode akzeptiert, lassen den
        Request serverseitig aber immer auf PARSE laufen; der Zeitbezug
        steckt ohnehin im Kontext.
        """
        return self._parse_connections(self._call("SearchOnTrip", {"ctxRecon": ctx_recon}))

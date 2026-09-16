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

from dataclasses import dataclass, field, replace
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


@dataclass
class Connection:
    dep_time: str
    arr_time: str
    num_changes: int  # Anzahl Fahrt-Abschnitte - 1 (Fußwege zählen nicht)
    legs: list[Leg] = field(default_factory=list)



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

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._req_counter = 0

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"{self._req_counter}@"

    def _call(self, meth: str, req: dict[str, Any]) -> dict[str, Any]:
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

    def stop_lines(self, stop_ext_id: str, max_journeys: int = 30) -> tuple[str, ...]:
        """Line labels with an upcoming departure at this stop, sorted."""
        lines = {dep.line for dep in self.station_board(stop_ext_id, max_journeys)}
        return tuple(sorted(lines - {"?"}, key=lambda name: (len(name), name)))

    def journey_details(self, jid: str) -> dict[str, Any]:
        """Fetch full stop-by-stop details for a single journey.

        `jid` comes from a Departure/journey object's "jid" field (not
        currently exposed on the Departure dataclass — extend station_board
        if you need it, or call _call("StationBoard", ...) directly and
        read jny["jid"]).

        Returns the raw `journey` dict (includes `stopL`: every stop with
        planned/realtime arrival+departure times and platform info).
        """
        res = self._call("JourneyDetails", {"jid": jid})
        return res.get("journey", {})

    def station_board(self, stop_ext_id: str, max_journeys: int = 10) -> list[Departure]:
        """Fetch the live departure board for a stop (by extId, see find_stops)."""
        res = self._call(
            "StationBoard",
            {
                "type": "DEP",
                "stbLoc": {"extId": stop_ext_id},
                "maxJny": max_journeys,
            },
        )
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
                    planned=stb.get("dTimeS", ""),
                    realtime=stb.get("dTimeR"),
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

        res = self._call("TripSearch", req)
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
                )
            )
        return connections

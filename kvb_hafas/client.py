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
from dataclasses import replace
from datetime import datetime
from typing import Any

import requests

from kvb_hafas.models import (
    Connection,
    Departure,
    JourneyRoute,
    JourneyStop,
    KVBHafasError,
    Leg,
    Line,
    Reachable,
    ScheduledJourney,
    ServerInfo,
    ServiceAlert,
    Stop,
    TripPage,
    Vehicle,
    WalkRoute,
)
from kvb_hafas.parsing import (
    _ani_track,
    _decode_polyline,
    _delay_minutes,
    _line_from_prod,
    _mast_steig,
    _prod_colours,
    _prod_name,
    station_ext_id,
)

ENDPOINT = "https://auskunft.kvb.koeln/gate"
# Public HAFAS access ID, served in cleartext by the KVB widget generator at
# https://auskunft.kvb.koeln/widgetgenerator.html. Shared by every widget user,
# not a per-user secret - no checksum or salt required. Secret scanners may flag
# it; that is a false positive. KVB can rotate or block it at any time.
AID = "Rt6foY5zcTTRXMQs"
USER_AGENT = "Mozilla/5.0 (compatible; kvb-hafas-client/0.1)"

# Verkehrsmittel-Bitmaske für den `jnyFltrL`-Filter von TripSearch. Die Werte
# sind das `cls`-Feld aus `common.prodL[]` — hier aus dem kompletten
# Linienkatalog (LineSearch) abgezählt, nicht geraten. 4 und 64 kommen im
# KVB-Datenbestand nicht vor.
PRODUCTS = {
    "s_bahn": 1,
    "stadtbahn": 2,  # catOut "Str"
    "bus": 8,  # inkl. Midi, Mini, SEV
    "regionalzug": 16,  # RB, RE, Regio
    "fernzug": 32,  # IC, ICE, THA
    "faehre": 128,
    "taxi": 256,  # AST und TAXI
}
ALL_PRODUCTS = sum(PRODUCTS.values())


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

    def _call_many(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any] | None]:
        """Mehrere Methoden in einem POST (`svcReqL` mit n Einträgen).

        Das spart Roundtrips und — wichtiger — die `min_interval`-Pause, die
        sonst pro Fahrt anfällt. Gibt pro Eintrag das `res` zurück, oder `None`
        wenn dieser Teil-Request nicht `OK` war; die Reihenfolge entspricht
        `calls`. Envelope-Fehler (der ganze POST kaputt) fliegen weiterhin.
        """
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
            "svcReqL": [{"meth": meth, "req": req} for meth, req in calls],
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
            raise KVBHafasError(f"batch failed at envelope level: {data['err']} ({data.get('errTxt', '')})")
        results = data.get("svcResL", [])
        if len(results) != len(calls):
            # Ohne diese Prüfung würde zip() in journey_routes() stillschweigend
            # Antworten den falschen jids zuordnen — und falsche Laufwege landen
            # in der DB. Lieber der ganze Block kaputt als still verfälscht.
            raise KVBHafasError(f"batch returned {len(results)} results for {len(calls)} requests")
        return [r.get("res") if r.get("err") == "OK" else None for r in results]

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
        (`900xxxxxx`) — pro Richtung/Bahnsteig eine eigene. Zum Zusammenfassen
        beider Richtungen steht die Master-ID in `JourneyStop.station_ext_id`
        (siehe station_ext_id()).
        """
        return self._parse_journey_route(self._call("JourneyDetails", {"jid": jid}), jid)

    def journey_routes(self, jids: list[str], chunk: int = 10) -> list[JourneyRoute]:
        """Laufwege vieler Fahrten, gebündelt (mehrere `svcReqL`-Einträge pro POST).

        Das Gate beantwortet mehrere Methoden in einem Request; für N Fahrten
        fallen so N/chunk statt N Roundtrips (und Rate-Limit-Pausen) an. Fahrten,
        deren Teil-Antwort nicht `OK` ist, fehlen im Ergebnis — der Rest kommt
        trotzdem an, sonst würde eine kaputte `jid` den ganzen Block kosten.
        """
        routes = []
        for start in range(0, len(jids), chunk):
            batch = jids[start : start + chunk]
            for jid, res in zip(batch, self._call_many([("JourneyDetails", {"jid": j}) for j in batch])):
                if res is not None:
                    routes.append(self._parse_journey_route(res, jid))
        return routes

    @staticmethod
    def _parse_journey_route(res: dict[str, Any], jid: str) -> JourneyRoute:
        """`JourneyDetails`-Antwort in eine JourneyRoute übersetzen."""
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
                    station_ext_id=station_ext_id(loc.get("extId", "")),
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

    def service_alerts(
        self,
        stop: Stop | None = None,
        line: str | None = None,
        max_num: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[ServiceAlert]:
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

        `max_num`: serverseitiges Limit auf die Anzahl Meldungen.

        `date_from`/`date_to` (YYYYMMDD) grenzen den Gültigkeitszeitraum
        ein — **kein Zugriff auf die Vergangenheit**: der HIM-Speicher hält
        nur aktuell gültige und künftige Meldungen, abgelaufene sind weg.
        Siehe docs/API.md#historische-daten.
        """
        req: dict[str, Any] = {"himFltrL": [{"type": "LINE", "mode": "INC", "value": line}] if line else []}
        if max_num is not None:
            req["maxNum"] = max_num
        if date_from:
            req["dateB"] = date_from
        if date_to:
            req["dateE"] = date_to
        res = self._call("HimSearch", req)
        common = res.get("common", {})
        alerts = []
        for msg in res.get("msgL", []):
            if stop is not None and not self._alert_matches_stop(msg, common, stop):
                continue
            stops, points = self._alert_geo(msg, common)
            alerts.append(
                ServiceAlert(
                    text=msg.get("text", "").strip(),
                    category=msg.get("cat", -1),
                    priority=msg.get("prio", -1),
                    valid_from=msg.get("sDate", ""),
                    valid_to=msg.get("eDate", ""),
                    head=msg.get("head", "").strip(),
                    hid=msg.get("hid", ""),
                    stops=stops,
                    points=points,
                )
            )
        return alerts

    @staticmethod
    def _alert_geo(msg: dict[str, Any], common: dict[str, Any]) -> tuple[tuple[str, ...], list[tuple[float, float]]]:
        """Betroffene Haltestellen und Koordinaten einer HIM-Meldung.

        HimGeoPos (alerts_in_area) kam für Köln immer leer zurück — der
        Geo-Bezug steckt stattdessen in den Loc-Referenzen der Meldung und in
        `himMsgEdgeL[].icoCrd`. Meldungen ohne beides (viele Aufzugsstörungen
        nennen ihre Haltestelle nur im Text) liefern leere Listen.
        """
        loc_l = common.get("locL", [])
        event_l, edge_l = common.get("himMsgEventL", []), common.get("himMsgEdgeL", [])

        loc_idx: list[int] = [i for i in (msg.get("fLocX"), msg.get("tLocX")) if i is not None]
        for ref in msg.get("eventRefL", []):
            if ref < len(event_l):
                loc_idx += [i for i in (event_l[ref].get("fLocX"), event_l[ref].get("tLocX")) if i is not None]

        names: list[str] = []
        points: list[tuple[float, float]] = []
        for idx in dict.fromkeys(loc_idx):
            if idx >= len(loc_l):
                continue
            loc = loc_l[idx]
            if loc.get("name") and loc["name"] not in names:
                names.append(loc["name"])
            crd = loc.get("crd") or {}
            if "x" in crd and "y" in crd:
                points.append((crd["y"] / 1_000_000, crd["x"] / 1_000_000))
        for ref in msg.get("edgeRefL", []):
            crd = edge_l[ref].get("icoCrd") if ref < len(edge_l) else None
            if crd and "x" in crd and "y" in crd:
                points.append((crd["y"] / 1_000_000, crd["x"] / 1_000_000))
        return tuple(names), list(dict.fromkeys(points))

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
        self,
        from_ext_id: str,
        to_ext_id: str,
        date: str | None = None,
        time: str | None = None,
        *,
        num: int | None = None,
        via_ext_id: str | None = None,
        products: int | None = None,
    ) -> list[Connection]:
        """Search for connections between two stops (by extId, see find_stops).

        `date`/`time` optional (YYYYMMDD / HHMMSS) — defaults to "now" if
        omitted. Subject to the same timetable-period limits as
        station_board(), see docs/API.md#historische-daten.

        `num` = gewünschte Anzahl Verbindungen (der Server liefert auch mal
        eine mehr), `via_ext_id` erzwingt einen Zwischenhalt, `products` ist
        eine Bitmaske aus PRODUCTS (z.B. `PRODUCTS["stadtbahn"] |
        PRODUCTS["bus"]`). Zum Blättern siehe trip_page().
        """
        return self.trip_page(
            from_ext_id, to_ext_id, date, time, num=num, via_ext_id=via_ext_id, products=products
        ).connections

    def trip_page(
        self,
        from_ext_id: str,
        to_ext_id: str,
        date: str | None = None,
        time: str | None = None,
        *,
        num: int | None = None,
        via_ext_id: str | None = None,
        products: int | None = None,
        scroll_ctx: str | None = None,
    ) -> TripPage:
        """Wie trip_search(), aber mit den Blätter-Tokens der Antwort.

        `scroll_ctx` nimmt `ctx_earlier`/`ctx_later` einer vorherigen Seite und
        liefert die Verbindungen davor bzw. danach — das ist das "früher/später"
        der offiziellen Auskunft. Die Locations müssen dabei mitgeschickt
        werden, der Token allein reicht nicht.

        Ein Filter, der für die Strecke nichts übrig lässt, endet in `H890`
        ("keine Verbindung gefunden") — das ist eine Antwort, kein Fehler
        unsererseits.
        """
        req: dict[str, Any] = {
            "depLocL": [{"extId": from_ext_id}],
            "arrLocL": [{"extId": to_ext_id}],
        }
        if date:
            req["outDate"] = date
        if time:
            req["outTime"] = time
        if num is not None:
            req["numF"] = num
        if via_ext_id:
            req["viaLocL"] = [{"loc": {"extId": via_ext_id}}]
        if products is not None:
            req["jnyFltrL"] = [{"type": "PROD", "mode": "INC", "value": str(products)}]
        if scroll_ctx:
            req["ctxScr"] = scroll_ctx

        res = self._call("TripSearch", req)
        return TripPage(
            connections=self._parse_connections(res),
            ctx_earlier=res.get("outCtxScrB", ""),
            ctx_later=res.get("outCtxScrF", ""),
        )

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
            # trfRes liefert den Rheinlandtarif-Preis gratis mit jeder TripSearch;
            # ovwTrfRefL zeigt auf den für die Verbindung gültigen Eintrag.
            ref = (con.get("ovwTrfRefL") or [{}])[0]
            fare_sets = con.get("trfRes", {}).get("fareSetL", [])
            fares = (fare_sets[ref.get("fareSetX", 0)] if ref.get("fareSetX", 0) < len(fare_sets) else {}).get("fareL", [])
            fare = fares[ref.get("fareX", 0)] if ref.get("fareX", 0) < len(fares) else {}
            connections.append(
                Connection(
                    dep_time=con.get("dep", {}).get("dTimeS", ""),
                    arr_time=con.get("arr", {}).get("aTimeS", ""),
                    num_changes=max(rides - 1, 0),
                    legs=legs,
                    ctx_recon=con.get("ctxRecon", ""),
                    fare_cents=fare.get("prc"),
                    fare_currency=fare.get("cur", ""),
                    fare_name=fare.get("name", ""),
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
        segments: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
    ) -> list[Vehicle]:
        """Live-Positionen aller Fahrzeuge in einer Bounding-Box (JourneyGeoPos).

        Positionen sind interpoliert (`trainPosMode: CALC`) — HAFAS rechnet
        sie aus Fahrplan plus Echtzeit-Prognose hoch, es sind keine
        GPS-Rohdaten. Die Box umfasst auch Regionalzüge im Bediengebiet.

        `Vehicle.track` enthält den `ani`-Block als (offset_ms, lat, lon) über
        die nächsten 120 Sekunden — damit lässt sich animieren, ohne erneut
        abzufragen (siehe map_server.py).

        `segments` (aus journey_segments()) lässt den Track dem echten
        Streckenverlauf folgen statt der Luftlinie zwischen zwei Halten.
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
        common = res.get("common", {})
        prod_l, loc_l = common.get("prodL", []), common.get("locL", [])
        ico_l = common.get("icoL", [])
        vehicles = []
        for jny in res.get("jnyL", []):
            pos = jny.get("pos")
            if not pos:
                continue
            prod_idx = jny.get("prodX")
            prod = prod_l[prod_idx] if prod_idx is not None and prod_idx < len(prod_l) else {}
            bearing = jny.get("dirGeo")
            ani = jny.get("ani") or {}
            next_loc_x = (ani.get("tLocX") or [None])[0]
            delay, next_stop = self._next_stop_delay(jny, loc_l, next_loc_x)
            colour, text_colour = _prod_colours(prod, ico_l)
            vehicles.append(
                Vehicle(
                    line=_prod_name(prod),
                    direction=jny.get("dirTxt", ""),
                    lat=pos["y"] / 1_000_000,
                    lon=pos["x"] / 1_000_000,
                    jid=jny.get("jid", ""),
                    category=prod.get("prodCtx", {}).get("catOut", "").strip(),
                    bearing=bearing if isinstance(bearing, int) else None,
                    delay=delay,
                    next_stop=next_stop,
                    colour=colour,
                    text_colour=text_colour,
                    track=_ani_track(ani, loc_l, segments),
                )
            )
        return vehicles

    @staticmethod
    def _next_stop_delay(
        jny: dict[str, Any], loc_l: list[dict[str, Any]], next_loc_x: int | None
    ) -> tuple[int | None, str]:
        """Verspätung und Name des Halts, auf den das Fahrzeug gerade zufährt.

        `ani.tLocX[0]` benennt diesen Halt; der passende Eintrag in `stopL`
        trägt Soll- und Ist-Zeit. Ohne Echtzeit-Prognose bleibt die Verspätung
        None — das ist etwas anderes als "pünktlich".
        """
        if next_loc_x is None or not (0 <= next_loc_x < len(loc_l)):
            return None, ""
        name = loc_l[next_loc_x].get("name", "")
        for stop in jny.get("stopL", []):
            if stop.get("locX") != next_loc_x:
                continue
            for planned, realtime in (("aTimeS", "aTimeR"), ("dTimeS", "dTimeR")):
                delay = _delay_minutes(stop.get(planned, ""), stop.get(realtime, ""))
                if delay is not None:
                    return delay, name
            break
        return None, name

    def journey_segments(self, jid: str) -> dict[tuple[str, str], list[tuple[float, float]]]:
        """Streckenverlauf einer Fahrt, aufgeteilt je Haltestellenpaar.

        `JourneyDetails` mit `getPolyline` liefert deutlich mehr Stützpunkte
        als journey_course() (Linie 18: 117 Punkte auf 30 Halte) und dazu
        `ppLocRefL`, also die Zuordnung Halt -> Punktindex. Damit lässt sich
        jede Fahrt zwischen zwei Halten dem tatsächlichen Verlauf folgen
        lassen; die Abschnitte gelten für jede Fahrt derselben Relation.

        Schlüssel sind die Mast-extIds beider Halte, wie sie auch
        JourneyGeoPos in `common.locL` verwendet.
        """
        return self._parse_segments(self._call("JourneyDetails", {"jid": jid, "getPolyline": True}))

    def journey_segments_many(self, jids: list[str], chunk: int = 10) -> list[dict[tuple[str, str], list[tuple[float, float]]]]:
        """Streckenverläufe vieler Fahrten, gebündelt — analog zu journey_routes().

        Ergebnis ist positionsgleich zu `jids`, damit der Aufrufer die
        Abschnitte weiter der Linie zuordnen kann, zu der er die jid geholt
        hat. Fahrten, deren Teil-Antwort nicht `OK` ist, liefern `{}` statt
        den ganzen Block zu kosten.
        """
        return [segments for segments, _ in self.journey_details_many(jids, chunk=chunk)]

    def journey_details_many(
        self, jids: list[str], chunk: int = 10
    ) -> list[tuple[dict[tuple[str, str], list[tuple[float, float]]], list[Stop]]]:
        """Streckenverlauf *und* Halte vieler Fahrten aus denselben Requests.

        Dieselbe Antwort trägt beides: die Polyline je Haltestellenpaar und in
        `common.locL` Name und Koordinate jedes Halts. Wer beides braucht, holt
        es hier in einem Rutsch statt zweimal dasselbe anzufragen. Ergebnis ist
        positionsgleich zu `jids`, nicht-`OK`-Teilantworten liefern `({}, [])`.
        """
        out: list[tuple[dict[tuple[str, str], list[tuple[float, float]]], list[Stop]]] = []
        for start in range(0, len(jids), chunk):
            batch = jids[start : start + chunk]
            calls = [("JourneyDetails", {"jid": j, "getPolyline": True}) for j in batch]
            out.extend(
                ({}, []) if res is None else (self._parse_segments(res), self._parse_stops(res))
                for res in self._call_many(calls)
            )
        return out

    @staticmethod
    def _parse_stops(res: dict[str, Any]) -> list[Stop]:
        """Halte einer `JourneyDetails`-Antwort mit Name und Koordinate.

        `common.locL` enthält neben den Halten auch Einträge ohne extId oder
        ohne Koordinate (Richtungstexte etwa) — die fliegen raus.
        """
        stops = []
        for loc in res.get("common", {}).get("locL", []):
            crd = loc.get("crd") or {}
            if not loc.get("extId") or "x" not in crd or "y" not in crd:
                continue
            stops.append(
                Stop(
                    name=loc.get("name", ""),
                    ext_id=loc["extId"],
                    lat=crd["y"] / 1_000_000,
                    lon=crd["x"] / 1_000_000,
                )
            )
        return stops

    @staticmethod
    def _parse_segments(res: dict[str, Any]) -> dict[tuple[str, str], list[tuple[float, float]]]:
        """`JourneyDetails`-Antwort mit Polyline in Abschnitte je Haltestellenpaar."""
        common = res.get("common", {})
        poly_l, loc_l = common.get("polyL", []), common.get("locL", [])
        if not poly_l or not poly_l[0].get("ppLocRefL"):
            return {}
        points = _decode_polyline(poly_l[0].get("crdEncYX", ""))
        refs = sorted(poly_l[0]["ppLocRefL"], key=lambda ref: ref.get("ppIdx", 0))
        segments: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for start, end in zip(refs, refs[1:]):
            from_x, to_x = start.get("locX"), end.get("locX")
            if from_x is None or to_x is None or from_x >= len(loc_l) or to_x >= len(loc_l):
                continue
            path = points[start.get("ppIdx", 0) : end.get("ppIdx", 0) + 1]
            if len(path) > 1:
                segments[(loc_l[from_x].get("extId", ""), loc_l[to_x].get("extId", ""))] = path
        return segments

    def find_lines(self, query: str) -> list[Line]:
        """Linien nach Label suchen (LineMatch).

        Achtung: Der Datenbestand reicht über die KVB hinaus — "1" trifft
        auch Linien aus VRR, Aachen und den Niederlanden. Die `line_id`
        (`de:vrs:...` für Köln/Bonn) zeigt, wer gemeint ist.
        """
        res = self._call("LineMatch", {"input": query})
        prod_l = res.get("common", {}).get("prodL", [])
        return [_line_from_prod(prod_l[ln["prodX"]], ln.get("lineId", "")) for ln in res.get("lineL", []) if ln.get("prodX", -1) < len(prod_l)]

    def all_lines(self, prefix: str = "") -> list[Line]:
        """Kompletter Linienkatalog in einem Request (LineSearch, leerer req).

        ~3100 Linien des ganzen Datenbestands, nicht nur KVB. `prefix` filtert
        clientseitig auf die `line_id` — "de:vrs" lässt die ~710 Linien aus
        Köln/Bonn übrig. Im Gegensatz zu find_lines() ohne Suchbegriff.
        """
        res = self._call("LineSearch", {})
        prod_l = res.get("common", {}).get("prodL", [])
        return [
            _line_from_prod(prod_l[ln["prodX"]], ln.get("lineId", ""))
            for ln in res.get("lineL", [])
            if ln.get("prodX", -1) < len(prod_l) and ln.get("lineId", "").startswith(prefix)
        ]

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

    def affected_stops(self) -> list[Stop]:
        """Haltestellen, die aktuell von einer Störung betroffen sind (HimMatch).

        Parameterlos — die Methode nimmt kein einziges Feld an. Liefert
        Steig-Einträge (extId `300xxxxxx`) ohne Koordinaten; für den
        Master-Eintrag den Namen über find_stops() nachschlagen.
        """
        res = self._call("HimMatch", {})
        stops, seen = [], set()
        for loc in res.get("affStL", []):
            ext_id = loc.get("extId", "")
            if ext_id in seen:
                continue
            seen.add(ext_id)
            crd = loc.get("crd") or {}
            has_crd = bool(crd.get("x") or crd.get("y"))
            stops.append(
                Stop(
                    name=loc.get("name", ""),
                    ext_id=ext_id,
                    lat=crd["y"] / 1_000_000 if has_crd else None,
                    lon=crd["x"] / 1_000_000 if has_crd else None,
                )
            )
        return stops

    def lines_in_area(self, lat: float, lon: float, radius_m: int = 500) -> list[Line]:
        """Alle Linien im Umkreis eines Punktes (LineGeoPos).

        Vollständiger als stop_details().lines — dort fehlen die Nachtlinien
        (Neumarkt: 9 vs. 14). Der Server deckelt bei 50 Linien pro Anfrage;
        für größere Gebiete in Kacheln abfragen.
        """
        res = self._call("LineGeoPos", {"ring": {"cCrd": {"x": round(lon * 1_000_000), "y": round(lat * 1_000_000)}, "maxDist": radius_m}})
        prod_l = res.get("common", {}).get("prodL", [])
        lines, seen = [], set()
        for entry in res.get("lineL", []):
            prod_idx = entry.get("prodX")
            if prod_idx is None or prod_idx >= len(prod_l):
                continue
            line = _line_from_prod(prod_l[prod_idx], entry.get("lineId", ""))
            if line.line_id in seen:
                continue
            seen.add(line.line_id)
            lines.append(line)
        return lines

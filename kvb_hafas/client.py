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
from dataclasses import dataclass
from typing import Any

import requests

ENDPOINT = "https://auskunft.kvb.koeln/gate"
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


@dataclass
class Departure:
    line: str
    direction: str
    planned: str  # HHMMSS-ish HAFAS time string, e.g. "224400"
    realtime: str | None
    platform: str | None
    cancelled: bool
    jid: str  # pass to client.journey_details() for the full stop sequence


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

    def nearby_stops(self, lat: float, lon: float, max_dist_m: int = 500, max_results: int = 10) -> list[Stop]:
        """Find stops/POIs within max_dist_m meters of the given coordinates.

        Note: without a location-type filter this also returns POIs (museums,
        landmarks, etc.), not just public transport stops. HAFAS supports a
        `locFltrL` product-type bitmask to restrict this to stops only; the
        exact bitmask for KVB hasn't been reverse-engineered yet (see
        docs/API.md).
        """
        res = self._call(
            "LocGeoPos",
            {
                "ring": {
                    "cCrd": {"x": round(lon * 1_000_000), "y": round(lat * 1_000_000)},
                    "maxDist": max_dist_m,
                },
                "maxLoc": max_results,
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
        return stops

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
                    platform=stb.get("dPlatfR") or stb.get("dPlatfS"),
                    cancelled=bool(jny.get("isCncl", False)),
                    jid=jny.get("jid", ""),
                )
            )
        return departures


if __name__ == "__main__":
    client = KVBHafasClient()
    stops = client.find_stops("Neumarkt")
    print(f"Gefunden: {[s.name for s in stops]}")
    if stops:
        deps = client.station_board(stops[0].ext_id)
        for d in deps:
            status = "AUSFALL" if d.cancelled else ""
            print(f"{d.planned} (rt:{d.realtime}) Linie {d.line} -> {d.direction} {status}")

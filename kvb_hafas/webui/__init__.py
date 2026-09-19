"""Bundled web UI assets (HTML/CSS/vendored Leaflet) for the KVB live map, departure board
and stats dashboard — served by `kvb_hafas.server.http_server` via importlib.resources.
The kvb-ha-map Home Assistant add-on runs that same server directly instead of shipping
its own copy.
"""

from __future__ import annotations

from kvb_hafas.webui.is_kvb_local import is_kvb_local

__all__ = ["is_kvb_local"]

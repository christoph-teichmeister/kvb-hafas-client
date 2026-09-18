"""Bundled web UI assets (HTML/CSS/vendored Leaflet) for the KVB live map, departure board
and stats dashboard — served by the kvb-ha-map Home Assistant add-on via importlib.resources.
"""

from __future__ import annotations

from kvb_hafas.webui.is_kvb_local import is_kvb_local

__all__ = ["is_kvb_local"]

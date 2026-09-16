"""Inoffizieller Python-Client für die HAFAS-Echtzeitdaten der KVB."""

from kvb_hafas.client import PRODUCTS, KVBHafasClient
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
from kvb_hafas.parsing import station_ext_id

__all__ = [
    "KVBHafasClient",
    "PRODUCTS",
    "Stop",
    "Departure",
    "JourneyRoute",
    "JourneyStop",
    "ServiceAlert",
    "Connection",
    "TripPage",
    "Leg",
    "Line",
    "Reachable",
    "ScheduledJourney",
    "ServerInfo",
    "Vehicle",
    "WalkRoute",
    "KVBHafasError",
    "station_ext_id",
]

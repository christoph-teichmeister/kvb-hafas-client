"""Tests für die Kartenserver-Logik (ohne Netz)."""

from __future__ import annotations

from kvb_hafas.server.http_server import _merge_segments
from kvb_hafas.server.stats import live_stats
from kvb_hafas.webui.is_kvb_local import css_class, is_kvb_local, is_kvb_tram


def test_merge_segments_keeps_finer_path():
    """Der Prefetch liefert für Züge nur zwei Punkte — der OSM-Verlauf muss stehen bleiben."""
    detailed = [(50.9, 6.9), (50.91, 6.91), (50.92, 6.92)]
    target = {("a", "b"): detailed}
    _merge_segments(target, {("a", "b"): [(50.9, 6.9), (50.92, 6.92)]})
    assert target[("a", "b")] == detailed


def test_merge_segments_adds_and_upgrades():
    target = {("a", "b"): [(50.9, 6.9), (50.92, 6.92)]}
    finer = [(50.9, 6.9), (50.91, 6.91), (50.92, 6.92)]
    _merge_segments(target, {("a", "b"): finer, ("b", "c"): [(1.0, 1.0), (2.0, 2.0)]})
    assert target[("a", "b")] == finer
    assert ("b", "c") in target


def test_css_class_buckets_tram_bus_rail():
    assert css_class("Str") == "tram"
    assert css_class("Bus") == "bus"
    assert css_class("S-Bahn") == "rail"
    assert css_class("RE") == "rail"
    assert css_class(None) == "rail"


def test_is_kvb_local_includes_tram_and_bus_only():
    assert is_kvb_local("Str") is True
    assert is_kvb_local("Bus") is True
    assert is_kvb_local("RE") is False


def test_is_kvb_tram_excludes_bus():
    assert is_kvb_tram("Str") is True
    assert is_kvb_tram("Bus") is False
    assert is_kvb_tram("S-Bahn") is False


def _vehicle(jid, line, category, delay=None, direction="Nach Irgendwo"):
    return {"jid": jid, "line": line, "category": category, "delay": delay, "direction": direction}


def test_live_stats_mode_filters_before_aggregation():
    vehicles = [
        _vehicle("t1", "1", "Str", delay=2),
        _vehicle("t2", "1", "Str", delay=9),
        _vehicle("b1", "146", "Bus", delay=20),
    ]
    stats = live_stats(vehicles, late_from=3, mode="tram")
    assert stats["vehicle_count"] == 2
    assert stats["vehicles_per_line"] == {"1": 2}
    assert stats["busiest_line"] == "1"
    assert stats["busiest_line_count"] == 2
    assert stats["max_current_delay_minutes"] == 9
    assert stats["max_current_delay_vehicle"] == {"jid": "t2", "line": "1", "direction": "Nach Irgendwo"}
    assert stats["delayed_vehicle_count"] == 1


def test_live_stats_without_mode_covers_all_vehicles():
    vehicles = [_vehicle("t1", "1", "Str", delay=2), _vehicle("b1", "146", "Bus", delay=20)]
    stats = live_stats(vehicles, late_from=3)
    assert stats["vehicle_count"] == 2
    assert stats["max_current_delay_minutes"] == 20


def test_live_stats_empty_vehicles_has_no_max_delay():
    stats = live_stats([], mode="tram")
    assert stats["vehicle_count"] == 0
    assert stats["busiest_line"] is None
    assert stats["max_current_delay_minutes"] is None
    assert stats["max_current_delay_vehicle"] is None

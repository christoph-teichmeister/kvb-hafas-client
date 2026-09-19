"""Tests für die Kartenserver-Logik (ohne Netz)."""

from __future__ import annotations

from kvb_hafas.server.http_server import _merge_segments


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

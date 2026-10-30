"""tools/fetch_rail_geometry.py: Abbruch ohne Eingabedatei und atomares Schreiben (ohne Netz)."""

import importlib.util
import json
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "fetch_rail_geometry.py"


@pytest.fixture
def tool(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("fetch_rail_geometry", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "GEOMETRY_FILE", tmp_path / "map_geometry.json")
    monkeypatch.setattr(mod, "RAIL_FILE", tmp_path / "rail_geometry.json")
    return mod


def test_missing_geometry_file_exits_1_with_message(tool, capsys):
    with pytest.raises(SystemExit) as exc:
        tool.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "map_geometry.json fehlt" in err
    assert not tool.RAIL_FILE.exists()


def test_write_atomic_replaces_file_and_leaves_no_temp(tool, tmp_path):
    target = tmp_path / "rail_geometry.json"
    target.write_text("alt", encoding="utf-8")

    tool.write_atomic(target, json.dumps({"a|b": [[1, 2]]}))

    assert json.loads(target.read_text(encoding="utf-8")) == {"a|b": [[1, 2]]}
    assert [p.name for p in tmp_path.iterdir()] == ["rail_geometry.json"]


def test_write_atomic_keeps_old_file_on_failure(tool, tmp_path, monkeypatch):
    target = tmp_path / "rail_geometry.json"
    target.write_text("alt", encoding="utf-8")

    def boom(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(tool.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        tool.write_atomic(target, "neu")

    assert target.read_text(encoding="utf-8") == "alt"
    assert [p.name for p in tmp_path.iterdir()] == ["rail_geometry.json"]


def test_main_without_todo_writes_atomically(tool, monkeypatch):
    """Nichts zu routen (keine geraden Abschnitte): kein Netzzugriff, Datei trotzdem geschrieben."""
    tool.GEOMETRY_FILE.write_text(json.dumps({"line_meta": {}, "line_paths": {}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(tool, "write_atomic", lambda path, text: calls.append((path, text)))
    monkeypatch.setattr(tool, "fetch_osm", lambda *a, **k: pytest.fail("kein Netzzugriff erwartet"))

    tool.main()

    assert calls == [(tool.RAIL_FILE, "{}")]

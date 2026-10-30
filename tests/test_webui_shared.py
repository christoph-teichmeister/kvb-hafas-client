"""Shared web UI helpers (kvb_hafas/webui/shared.js): escaping, colour
validation, fetch error handling, visibility-aware polling — plus static
checks that every page uses them. JS parts run under Node (skipped if absent).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parent.parent / "kvb_hafas" / "webui"
SHARED_JS = WEBUI / "shared.js"
PAGES = ["index.html", "map.html", "departures.html", "dashboard.html"]
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_node(script: str):
    out = subprocess.run(
        [NODE, "-e", script, str(SHARED_JS)],
        env={**os.environ, "TZ": "UTC"},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_esc_neutralises_markup_and_attribute_breakouts():
    result = run_node(
        """
const { esc } = require(process.argv[1]);
console.log(JSON.stringify([
  esc('<img src=x onerror="alert(1)">'),
  esc("' onmouseover='alert(1)"),
  esc('a & b `c`'),
  esc(null), esc(undefined), esc(0), esc(42),
]));
"""
    )
    assert result[0] == "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
    assert result[1] == "&#39; onmouseover=&#39;alert(1)"
    assert result[2] == "a &amp; b &#96;c&#96;"
    assert result[3:] == ["", "", "0", "42"]
    for s in result:
        assert not re.search(r"[<>\"']", s)


@needs_node
def test_safe_colour_only_accepts_hex():
    result = run_node(
        """
const { safeColour } = require(process.argv[1]);
console.log(JSON.stringify([
  '#fff', '#A1b2C3', '#abcd', 'red', '#fff;background:url(x)', '" onclick="x',
  'expression(alert(1))', null, 123, '#12345g',
].map(c => safeColour(c, '#888'))));
"""
    )
    assert result == ["#fff", "#A1b2C3"] + ["#888"] * 8


@needs_node
def test_fetch_json_reports_server_errors_and_non_json():
    result = run_node(
        """
const shared = require(process.argv[1]);
const reply = (status, type, body) => async () => ({
  ok: status >= 200 && status < 300, status, statusText: 'X',
  headers: { get: (h) => h.toLowerCase() === 'content-type' ? type : null },
  json: async () => JSON.parse(body),
});
const cases = [
  reply(200, 'application/json', '{"a": 1}'),
  reply(502, 'application/json; charset=utf-8', '{"error": "HAFAS nicht erreichbar"}'),
  reply(500, 'text/html', '<html>oops</html>'),
  reply(200, 'text/html', '<html>login</html>'),
  reply(200, 'application/json', 'not json'),
  async () => { throw new TypeError('Failed to fetch'); },
];
(async () => {
  const out = [];
  for (const c of cases) {
    globalThis.fetch = c;
    try { out.push({ ok: await shared.fetchJson('api/x') }); }
    catch (err) { out.push({ err: shared.friendlyError(err), status: err.status ?? null }); }
  }
  console.log(JSON.stringify(out));
})();
"""
    )
    assert result[0] == {"ok": {"a": 1}}
    assert result[1] == {"err": "HAFAS nicht erreichbar", "status": 502}
    assert result[2] == {"err": "Serverfehler 500 (X)", "status": 500}
    assert result[3]["err"].startswith("Unerwartete Antwort")
    assert result[4]["err"].startswith("Unerwartete Antwort")
    assert result[5]["err"].startswith("Netzwerkfehler")


@needs_node
def test_poll_while_visible_pauses_when_hidden():
    result = run_node(
        """
const listeners = [];
globalThis.document = {
  hidden: false,
  addEventListener: (t, fn) => listeners.push(fn),
  removeEventListener: (t, fn) => listeners.splice(listeners.indexOf(fn), 1),
};
const { pollWhileVisible } = require(process.argv[1]);
let calls = 0;
const p = pollWhileVisible(() => { calls++; }, 20);
const fire = () => listeners.forEach(fn => fn());
const wait = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  await wait(70);
  const visible = calls;
  document.hidden = true; fire();
  const atHide = calls;
  await wait(80);
  const hidden = calls - atHide;
  document.hidden = false; fire();
  const resumedImmediately = calls === atHide + 1;
  p.stop();
  const atStop = calls;
  await wait(60);
  console.log(JSON.stringify({ visible, hidden, resumedImmediately,
                               afterStop: calls - atStop, listeners: listeners.length }));
})();
"""
    )
    assert result["visible"] >= 2
    assert result["hidden"] == 0
    assert result["resumedImmediately"] is True
    assert result["afterStop"] == 0
    assert result["listeners"] == 0


@pytest.mark.parametrize("page", PAGES)
def test_pages_load_shared_js_relatively_before_inline_script(page):
    html = (WEBUI / page).read_text(encoding="utf-8")
    tag = '<script src="shared.js"></script>'
    assert tag in html
    inline = html.index("<script>")
    assert html.index(tag) < inline
    # Ingress: no absolute URLs for own assets/API.
    assert not re.search(r"""(src|href)=["']/(?!/)""", html)
    assert not re.search(r"""fetch\(\s*['"`]/""", html)
    # Helpers live in shared.js only.
    for fn in ("function friendlyError", "function esc(", "function minutesUntil", "function berlinNow"):
        assert fn not in html, f"{page} still defines {fn}"
    # Raw fetch() is wrapped by fetchJson everywhere.
    assert "await fetch(" not in html


def test_vendor_versions_documented():
    text = (WEBUI / "vendor" / "VERSIONS.md").read_text(encoding="utf-8")
    import hashlib

    for f in sorted((WEBUI / "vendor").rglob("*")):
        if f.is_file() and f.name != "VERSIONS.md":
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            assert digest in text, f"sha256 of {f.name} missing/outdated in VERSIONS.md"

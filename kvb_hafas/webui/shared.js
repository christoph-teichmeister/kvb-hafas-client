// Shared helpers for all web UI pages (index, map, departures, dashboard).
// Loaded as a classic script via a relative URL (`<script src="shared.js">`),
// so it works behind HA Ingress's dynamic base path; the top-level
// declarations below are globals for the page's inline script. Also
// `require()`-able from Node for tests/test_webui_*.py.

// --- Escaping ------------------------------------------------------------
// Everything from the server, HAFAS or localStorage that ends up in an HTML
// string (innerHTML, Leaflet popups/divIcons, template strings) goes through
// esc() — safe for text content and for quoted attribute values.
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };

function esc(s) {
  return String(s ?? '').replace(/[&<>"'`]/g, c => ESC_MAP[c]);
}

// Only plain hex colours (#rgb / #rrggbb) may reach a style attribute;
// anything else (e.g. `red;background:url(...)`) falls back to `fallback`.
function safeColour(colour, fallback = null) {
  return typeof colour === 'string' && /^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(colour) ? colour : fallback;
}

// --- Errors / fetch ------------------------------------------------------

function friendlyError(err) {
  if (!err) return 'Unbekannter Fehler';
  if (err.name === 'AbortError') return 'Anfrage abgebrochen';
  if (err instanceof TypeError) return 'Netzwerkfehler — ist der Server erreichbar?';
  return err.message || 'Unbekannter Fehler';
}

// fetch() + JSON with uniform error handling: non-2xx responses throw (with
// the server's `{"error": ...}` text if there is one), and so does a non-JSON
// body — e.g. an HA login/error page served by Ingress with status 200.
async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const type = (res.headers && res.headers.get('content-type')) || '';
  let data = null;
  if (/json/i.test(type)) {
    try { data = await res.json(); } catch (err) { data = null; }
  }
  if (!res.ok) {
    const serverMsg = data && typeof data.error === 'string' && data.error;
    const err = new Error(serverMsg || `Serverfehler ${res.status}${res.statusText ? ` (${res.statusText})` : ''}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  if (data === null || typeof data !== 'object') {
    const err = new Error('Unerwartete Antwort vom Server (kein JSON)');
    err.status = res.status;
    throw err;
  }
  return data;
}

// --- Polling -------------------------------------------------------------
// setInterval that pauses while the tab is hidden and, on return, runs `fn`
// once right away before resuming the interval. Does not call `fn` on start.
// Returns { stop(), restart() }; restart() runs `fn` now and resets the clock.
function pollWhileVisible(fn, ms) {
  let timer = null;
  let stopped = false;
  const hidden = () => typeof document !== 'undefined' && document.hidden;
  const start = () => {
    if (timer === null && !stopped && !hidden()) timer = setInterval(fn, ms);
  };
  const halt = () => { if (timer !== null) clearInterval(timer); timer = null; };
  const onVisibility = () => {
    if (stopped) return;
    if (hidden()) { halt(); return; }
    if (timer === null) { fn(); start(); }
  };
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
  start();
  return {
    stop() {
      stopped = true;
      halt();
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
    },
    restart() {
      halt();
      fn();
      start();
    },
  };
}

// --- Cologne time --------------------------------------------------------
// HAFAS times are Cologne wall-clock (HHMMSS; a day-offset prefix refers to
// the journey's operating day, so it is ignored). Compare against
// Europe/Berlin, not the device's timezone — otherwise a phone abroad
// (e.g. Greece, +1h) sees every departure within the next hour as already
// gone ("Sofort").
const BERLIN_TZ = 'Europe/Berlin';
const BERLIN_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: BERLIN_TZ, hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});
const WEEKDAY_INDEX = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

function berlinNow(date = new Date()) {
  const p = {};
  for (const { type, value } of BERLIN_PARTS.formatToParts(date)) p[type] = value;
  return {
    year: +p.year, month: +p.month, day: +p.day, weekday: WEEKDAY_INDEX[p.weekday],
    hour: +p.hour, minute: +p.minute, second: +p.second,
  };
}

// Accepts HHMM, HHMMSS or DDHHMMSS (day offset ignored); anything else -> null.
function parseHafasTime(hafas) {
  if (typeof hafas !== 'string' || !/^\d+$/.test(hafas)) return null;
  let hh, mm;
  if (hafas.length === 4) { hh = hafas.slice(0, 2); mm = hafas.slice(2, 4); }
  else if (hafas.length === 6 || hafas.length === 8) { hh = hafas.slice(-6, -4); mm = hafas.slice(-4, -2); }
  else return null;
  const h = parseInt(hh, 10), m = parseInt(mm, 10);
  if (h > 47 || m > 59) return null;
  return { hour: h, minute: m };
}

// Minutes from now (Cologne) until a HAFAS time, folded across midnight to
// the nearest occurrence: 23:50 -> 00:05 is +15, 00:01 -> 23:59 is -2.
function minutesUntil(hafas, date = new Date()) {
  const t = parseHafasTime(hafas);
  if (!t) return null;
  const now = berlinNow(date);
  const nowMin = now.hour * 60 + now.minute + now.second / 60;
  let diff = Math.round(t.hour * 60 + t.minute - nowMin);
  diff = ((diff % 1440) + 1440) % 1440;  // 0..1439
  if (diff > 720) diff -= 1440;
  return diff;
}

// Locale formatting pinned to Cologne time (dashboard/map timestamps).
function fmtBerlin(date, opts) {
  return new Date(date).toLocaleString('de-DE', { timeZone: BERLIN_TZ, ...opts });
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    esc, safeColour, friendlyError, fetchJson, pollWhileVisible,
    BERLIN_PARTS, berlinNow, parseHafasTime, minutesUntil, fmtBerlin,
  };
}

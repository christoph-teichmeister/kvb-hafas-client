"""Countdown maths in the web UI must use Cologne time, not the device timezone.

Loads kvb_hafas/webui/shared.js (the helpers every page includes) under Node
with several `TZ` values; skipped when Node is not installed.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parent.parent / "kvb_hafas" / "webui"
SHARED_JS = WEBUI / "shared.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_node(script: str, *args: str, tz: str = "Europe/Berlin"):
    out = subprocess.run(
        [NODE, "-e", script, str(SHARED_JS), *args],
        env={**os.environ, "TZ": tz},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


HARNESS = """
const f = require(process.argv[1]);
const at = (iso) => new Date(iso);
console.log(JSON.stringify({
  summer: [f.minutesUntil('160500', at('2026-09-26T13:55:00Z')),
           f.minutesUntil('155400', at('2026-09-26T13:55:00Z'))],
  winter: f.minutesUntil('131000', at('2026-01-10T12:00:00Z')),
  midnight: [f.minutesUntil('000500', at('2026-09-26T21:50:00Z')),
             f.minutesUntil('01000500', at('2026-09-26T21:50:00Z'))],
  // 00:01 in Cologne: a 23:59 departure is 2 minutes gone, not 1438 ahead.
  pastMidnight: [f.minutesUntil('235900', at('2026-09-26T22:01:00Z')),
                 f.minutesUntil('2359', at('2026-09-26T22:01:00Z'))],
  // 23:59 in Cologne: 00:01 is 2 minutes ahead.
  beforeMidnight: f.minutesUntil('000100', at('2026-09-26T21:59:00Z')),
  // A departure 90 min ago stays in the past (not "tomorrow").
  longGone: f.minutesUntil('140000', at('2026-09-26T13:30:00Z')),
  hhmm: f.minutesUntil('1605', at('2026-09-26T13:55:00Z')),
  invalid: ['', null, undefined, '16:05', '123', '12345', 'abcdef', '1675', 1605]
    .map(x => f.minutesUntil(x, at('2026-09-26T13:55:00Z'))),
  clock: f.berlinNow(at('2026-09-26T13:55:00Z')),
  fmt: f.fmtBerlin(at('2026-09-26T22:30:00Z'), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }),
}));
"""


@pytest.mark.parametrize("tz", ["Europe/Berlin", "Europe/Athens", "UTC", "America/New_York"])
def test_minutes_until_ignores_device_timezone(tz):
    result = run_node(HARNESS, tz=tz)
    # 15:55 in Cologne (16:55 in Athens): 16:05 is 10 min away, 15:54 just gone.
    assert result["summer"] == [10, -1]
    assert result["winter"] == 10  # CET, 13:00 local
    assert result["midnight"] == [15, 15]  # 23:50 -> 00:05 next day
    assert result["pastMidnight"] == [-2, -2]
    assert result["beforeMidnight"] == 2
    assert result["longGone"] == -90
    assert result["hhmm"] == 10
    assert result["invalid"] == [None] * 9
    assert result["clock"]["hour"] == 15 and result["clock"]["minute"] == 55
    assert result["clock"]["weekday"] == 6  # Saturday
    # 22:30 UTC is already the next day in Cologne.
    assert result["fmt"] == "27.09., 00:30"


BOARD_HARNESS = """
Object.assign(globalThis, require(process.argv[1]));
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('function groupByPlatform');
const end = src.indexOf('\\n}\\n', src.indexOf('function upcoming')) + 3;
const f = new Function(src.slice(start, end)
  + '; return { upcoming, platformLabel, groupByPlatform };')();
const now = new Date('2026-09-26T13:55:00Z'); // 15:55 in Cologne
const deps = [
  { line: '5', planned: '154000', realtime: '154000' },  // gone
  { line: '5', planned: '155000', realtime: '155500' },  // delayed, due now
  { line: '5', planned: '160500', realtime: null },      // planned only
  { line: '5', planned: '155400', realtime: '155400' },  // just gone
  { line: '5', planned: '', realtime: null },            // unparseable
];
console.log(JSON.stringify({
  upcoming: f.upcoming(deps, now).map(d => d.planned),
  labels: ['Steig 1', '2', 'A', '', null].map(f.platformLabel),
  order: f.groupByPlatform([{ platform: 'Steig 10' }, { platform: null },
                            { platform: 'Steig 2' }]).map(([k]) => k),
}));
"""


@pytest.mark.parametrize("tz", ["Europe/Berlin", "Europe/Athens"])
def test_board_hides_past_departures_and_labels_platforms(tz):
    result = run_node(BOARD_HARNESS, str(WEBUI / "departures.html"), tz=tz)
    assert result["upcoming"] == ["155000", "160500", ""]
    assert result["labels"] == ["Steig 1", "Steig 2", "Steig A", "", ""]
    assert result["order"] == ["Steig 2", "Steig 10", ""]

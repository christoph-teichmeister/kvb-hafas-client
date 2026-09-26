"""Countdown maths in the web UI must use Cologne time, not the device timezone.

Runs the inline JS of departures.html/index.html under Node with several
`TZ` values; skipped when Node is not installed.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parent.parent / "kvb_hafas" / "webui"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

HARNESS = """
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('const BERLIN_PARTS');
const end = src.indexOf('\\n}\\n', src.indexOf('function minutesUntil')) + 3;
const f = new Function(src.slice(start, end) + '; return { minutesUntil, berlinNow };')();
const at = (iso) => new Date(iso);
console.log(JSON.stringify({
  summer: [f.minutesUntil('160500', at('2026-09-26T13:55:00Z')),
           f.minutesUntil('155400', at('2026-09-26T13:55:00Z'))],
  winter: f.minutesUntil('131000', at('2026-01-10T12:00:00Z')),
  midnight: [f.minutesUntil('000500', at('2026-09-26T21:50:00Z')),
             f.minutesUntil('01000500', at('2026-09-26T21:50:00Z'))],
  clock: f.berlinNow(at('2026-09-26T13:55:00Z')),
}));
"""


@pytest.mark.parametrize("page", ["departures.html", "index.html"])
@pytest.mark.parametrize("tz", ["Europe/Berlin", "Europe/Athens", "UTC", "America/New_York"])
def test_minutes_until_ignores_device_timezone(page, tz):
    out = subprocess.run(
        [NODE, "-e", HARNESS, str(WEBUI / page)],
        env={**os.environ, "TZ": tz},
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout)
    # 15:55 in Cologne (16:55 in Athens): 16:05 is 10 min away, 15:54 just gone.
    assert result["summer"] == [10, -1]
    assert result["winter"] == 10  # CET, 13:00 local
    assert result["midnight"] == [15, 15]  # 23:50 -> 00:05 next day
    assert result["clock"]["hour"] == 15 and result["clock"]["minute"] == 55
    assert result["clock"]["weekday"] == 6  # Saturday


BOARD_HARNESS = """
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('const BERLIN_PARTS');
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
    out = subprocess.run(
        [NODE, "-e", BOARD_HARNESS, str(WEBUI / "departures.html")],
        env={**os.environ, "TZ": tz},
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout)
    assert result["upcoming"] == ["155000", "160500", ""]
    assert result["labels"] == ["Steig 1", "Steig 2", "Steig A", "Ohne Steig-Angabe", "Ohne Steig-Angabe"]
    assert result["order"] == ["Steig 2", "Steig 10", ""]

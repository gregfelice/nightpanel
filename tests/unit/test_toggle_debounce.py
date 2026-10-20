# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for nightpanel-toggle's spurious-revert debounce.

The toggle script (~/.local/bin/nightpanel-toggle) is invoked once per
panel-button press. Because PanelMenu.Button re-dispatches clicks within
its stale-grab window, a press can spawn the script TWICE: once for the
real press (apply) and again ~5 s later when the user clicks any other
window (would revert, but the user didn't intend it).

The script debounces that second invocation by checking ACTIVE_FILE's
mtime: a revert request younger than NP_DEBOUNCE_S is dropped.

These tests run the actual script in NP_TOGGLE_MOCK=1 mode so the
orchestrator is never invoked — only the decision state machine is
exercised. State paths are redirected to tmp_path so the real user
config is untouched, and a short debounce window keeps the suite fast.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Resolve the installed toggle from PATH (system pkg → /usr/bin), falling back to
# the dev-stow location. The autouse script_exists fixture skips when neither is
# present (e.g. CI without install).
SCRIPT = Path(
    shutil.which("nightpanel-toggle") or Path.home() / ".local" / "bin" / "nightpanel-toggle"
)
DEBOUNCE_S = 0.5  # the script caps this at toggle.GUARD_CEIL_S (0.2 s)

# The tests set the active file's mtime instead of sleeping. A sleep races the subprocess's
# interpreter startup, which on a loaded machine alone outlasts the 0.2 s window and turns a
# "quick" second press into a legitimate revert.
FRESH_S = 60.0  # mtime this far in the future: age is negative, always inside the window
STALE_S = 60.0  # mtime this far in the past: always outside the window


@pytest.fixture
def env(tmp_path):
    """Isolated config dir + short debounce + mock mode for every test."""
    base = {**os.environ}
    # Strip anything that might collide; tests must be hermetic.
    for k in ("NP_CONFIG_DIR", "NP_DEBOUNCE_S", "NP_TOGGLE_MOCK"):
        base.pop(k, None)
    return {
        **base,
        "NP_CONFIG_DIR": str(tmp_path),
        "NP_DEBOUNCE_S": str(DEBOUNCE_S),
        "NP_TOGGLE_MOCK": "1",
    }


def _invoke(env):
    r = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"toggle script failed: {r.stderr}"
    return r


def _age_active(tmp_path, offset_s: float) -> None:
    """Set nightpanel-active's mtime to now + offset_s."""
    t = time.time() + offset_s
    os.utime(tmp_path / "nightpanel-active", (t, t))


def _active(tmp_path) -> bool:
    return (tmp_path / "nightpanel-active").exists()


def _last_decision(tmp_path) -> str:
    text = (tmp_path / "toggle.log").read_text()
    for line in reversed(text.splitlines()):
        if "decision:" in line:
            return line.split("decision:", 1)[1].strip()
    return "(none)"


@pytest.fixture(autouse=True)
def script_exists():
    """Skip cleanly if the launcher isn't installed (e.g. CI without the dev tree)."""
    if not SCRIPT.exists():
        pytest.skip(f"toggle launcher missing at {SCRIPT}")


def test_cold_press_applies(env, tmp_path):
    """First press with no ACTIVE_FILE: apply, file appears."""
    _invoke(env)
    assert _active(tmp_path)
    assert _last_decision(tmp_path) == "apply"


def test_quick_second_press_is_debounced(env, tmp_path):
    """Press within the debounce window after apply: revert is suppressed.

    This is the bug being fixed — without the debounce, the re-dispatched
    click reverts immediately after the real press.
    """
    _invoke(env)
    _age_active(tmp_path, FRESH_S)
    _invoke(env)
    assert _active(tmp_path), "ACTIVE_FILE must persist through a debounced revert"
    assert _last_decision(tmp_path) == "debounce-revert"


def test_revert_after_window_proceeds(env, tmp_path):
    """Press well after the window: legitimate revert proceeds."""
    _invoke(env)
    _age_active(tmp_path, -STALE_S)
    _invoke(env)
    assert not _active(tmp_path)
    assert _last_decision(tmp_path) == "revert"


def test_full_round_trip(env, tmp_path):
    """apply → wait → revert → quickly apply again."""
    _invoke(env)
    _age_active(tmp_path, -STALE_S)
    _invoke(env)
    assert not _active(tmp_path)
    _invoke(env)  # ACTIVE_FILE absent → apply, no debounce involved
    assert _active(tmp_path)
    assert _last_decision(tmp_path) == "apply"


def test_log_records_each_invocation(env, tmp_path):
    """toggle.log accumulates one `invoked` line per spawn — necessary
    for post-hoc diagnosis of any future toggle anomaly."""
    _invoke(env)
    _invoke(env)
    log = (tmp_path / "toggle.log").read_text()
    invocations = [line for line in log.splitlines() if "=== invoked" in line]
    assert len(invocations) == 2

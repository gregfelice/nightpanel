# SPDX-License-Identifier: GPL-3.0-or-later
"""nightpanel-toggle — flip the system-wide nightpanel theme on/off.

The shipped entry point for the toggle (console script `nightpanel-toggle`, and the
`bindir` launcher installed by meson). Invoked once per GNOME panel-button press and by
the CLI. Drives the orchestrator via a plain package import — no repo checkout, no
NIGHTPANEL_HOME, no sys.path surgery — so it works from any installed location.

Implements the spurious-revert debounce the panel button needs: PanelMenu.Button can
re-dispatch a click within its stale-grab window, spawning this twice within a few tens
of ms; we swallow only those. A *deliberate* retap (on -> look -> off, ~200 ms+ apart)
must always toggle — a longer window is the "i have to click twice to toggle" bug, so the
guard is capped at GUARD_CEIL_S (see tests/unit/test_toggle_policy.py).

Environment:
  NP_CONFIG_DIR    state dir for nightpanel-active + toggle.log (default ~/.config/nightpanel)
  NP_DEBOUNCE_S    guard seconds; only ever *tightens* the cap below
  NP_TOGGLE_MOCK   "1" -> decision state machine only, orchestrator never invoked (tests)
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from pathlib import Path

# Longest gap that can only be an accidental/re-dispatched double-tap; anything slower is a
# deliberate retap and MUST toggle. NP_DEBOUNCE_S can tighten the guard but never loosen it
# past this ceiling (loosening reintroduces the "click twice to toggle" UX bug).
GUARD_CEIL_S = 0.2


def _config_dir() -> Path:
    return Path(os.environ.get("NP_CONFIG_DIR") or (Path.home() / ".config" / "nightpanel"))


def _guard_seconds() -> float:
    try:
        return min(float(os.environ.get("NP_DEBOUNCE_S", GUARD_CEIL_S)), GUARD_CEIL_S)
    except ValueError:
        return GUARD_CEIL_S


def _logline(log: Path, msg: str) -> None:
    ts = datetime.datetime.now().isoformat()
    with log.open("a") as f:
        f.write(f"{ts} pid={os.getpid()} ppid={os.getppid()} {msg}\n")


def drive(method: str) -> None:
    """Run the orchestrator (skipped in mock mode so tests exercise only policy).

    Plain package import: works wherever `nightpanel` is installed (site-packages, distro
    package, dev venv). No NIGHTPANEL_HOME / sys.path insertion.
    """
    if os.environ.get("NP_TOGGLE_MOCK") == "1":
        return
    from nightpanel.services.nightpanel_orchestrator import NightpanelOrchestrator

    getattr(NightpanelOrchestrator(), method)()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    # nightpanel-active is the same file the GNOME shell extension watches; keep the name.
    active = config_dir / "nightpanel-active"
    log = config_dir / "toggle.log"
    guard_s = _guard_seconds()

    # argv repr is logged verbatim so bin/analyze-toggle-log can recover the event metadata
    # the shell extension forwards (--evt-time, --evt-button, ...).
    _logline(log, f"=== invoked argv={argv!r}")

    from nightpanel.services.nightpanel_orchestrator import OrchestratorError

    now = time.time()
    try:
        if not active.exists():
            decision = "apply"
            drive("apply")
            active.touch()
        else:
            age_s = now - active.stat().st_mtime
            if age_s < guard_s:
                # Spurious re-dispatch of the press that just turned us on — keep state.
                decision = "debounce-revert"
            else:
                decision = "revert"
                drive("revert")
                active.unlink(missing_ok=True)
    except OrchestratorError as e:
        # A failure is still an outcome, so the entry point has to record it as one.
        # Nothing here may claim a decision happened: the orchestrator refused to save
        # state, so nightpanel-active is left untouched rather than marked on top of a
        # missing baseline. An uncaught exception would leave toggle.log with an
        # invocation and no decision line, and bin/analyze-toggle-log only counts
        # invocations that produced a decision — the failure would be invisible to the
        # tool built to diagnose toggles. Rendering it is the UI work in the BACKLOG.
        _logline(log, f"decision: failed ({e})")
        return 1

    _logline(log, f"decision: {decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

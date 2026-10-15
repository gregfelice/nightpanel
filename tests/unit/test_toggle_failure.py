# SPDX-License-Identifier: GPL-3.0-or-later
"""The toggle entry point reports an orchestrator failure as an outcome.

The contract says a failure is an outcome, not a swallowed warning, and the orchestrator
now raises rather than logging. What was still open is the entry point: a failed state
write raises out of `drive()`, and an uncaught exception leaves `toggle.log` with an
invocation and no decision line — and `bin/analyze-toggle-log` only counts invocations
that produced a decision, so the failure is invisible to the tool built to diagnose
toggles.

These tests drive `main()` in process with the orchestrator replaced, so nothing touches
the real config dir and the debounce tests' mock mode is not involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nightpanel import toggle
from nightpanel.services.nightpanel_orchestrator import OrchestratorError


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A window of 0 so the revert branch is always taken — these tests are about the
    failure, not about the debounce."""
    monkeypatch.setenv("NP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NP_DEBOUNCE_S", "0")
    return tmp_path


def _decisions(tmp_path: Path) -> list[str]:
    text = (tmp_path / "toggle.log").read_text()
    return [
        line.split("decision:", 1)[1].strip() for line in text.splitlines() if "decision:" in line
    ]


def test_a_failed_state_write_is_reported_and_not_marked_active(env: Path, monkeypatch) -> None:
    def failing_drive(method: str) -> None:
        raise OrchestratorError("could not save state: read-only filesystem")

    monkeypatch.setattr(toggle, "drive", failing_drive)
    code = toggle.main([])

    assert code == 1, "a failed toggle must exit non-zero"
    assert not (env / "nightpanel-active").exists(), (
        "the toggle must not mark the world active when the orchestrator refused to save state"
    )
    decisions = _decisions(env)
    assert any(d.startswith("failed") for d in decisions), (
        f"toggle.log must record the failure as a decision so analyze-toggle-log can see it; "
        f"got {decisions}"
    )


def test_a_failed_revert_leaves_active_state_as_it_was(env: Path, monkeypatch) -> None:
    """The world is still on when the revert failed, so the state file must still say on."""

    def failing_drive(method: str) -> None:
        raise OrchestratorError("state file is corrupt")

    monkeypatch.setattr(toggle, "drive", failing_drive)
    (env / "nightpanel-active").touch()
    code = toggle.main([])

    assert code == 1
    assert (env / "nightpanel-active").exists(), (
        "a failed revert must not clear nightpanel-active; the tool is still engaged"
    )
    assert any(d.startswith("failed") for d in _decisions(env))

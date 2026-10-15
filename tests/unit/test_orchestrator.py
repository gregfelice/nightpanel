# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for NightpanelOrchestrator state machine.

Locks in the snapshot-guard + ≥1-success-required-to-mark-active behavior
patched on 2026-05-25 after observing that ACTIVE_FILE drift could cause
the next apply() to capture the already-applied palette as the baseline,
and that a fully-failed apply() still touched ACTIVE_FILE.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the orchestrator resolves its state
file, ACTIVE_FILE and command file through the same ``Paths`` the adapters do, so
there is nothing to patch. The test double raises ``AdapterError`` because the
orchestrator only catches ``AdapterError``/``AdapterNotLive`` — a bare RuntimeError
would escape the loop instead of being recorded as an outcome, which is the very
behaviour these tests are about.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import pytest

from nightpanel.adapters.base import Adapter, AdapterError
from nightpanel.adapters.firefox import FirefoxAdapter
from nightpanel.palette import NIGHTPANEL, Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner
from nightpanel.services import nightpanel_orchestrator as orch


class FakeAdapter(Adapter):
    """Adapter test double — records every call, configurable apply outcome."""

    def __init__(
        self,
        name: str,
        *,
        raises: bool = False,
        verify_on: bool = False,
        stateful: bool = True,
    ):
        self.name = name
        self.raises = raises
        self._verify_on = verify_on
        self.stateful = stateful
        self._applied = False
        self.snapshots_taken = 0
        self.applies = 0
        self.reverts = 0
        self.last_revert_snapshot: dict | None = None
        self._installed = True

    def installed(self) -> bool:
        return self._installed

    def snapshot(self) -> dict:
        self.snapshots_taken += 1
        if self.raises:
            raise AdapterError(f"{self.name}: capture boom")
        return {"name": self.name, "captured": True}

    def apply(self, palette: Palette) -> None:
        self.applies += 1
        if self.raises:
            raise AdapterError(f"{self.name} apply boom")
        self._applied = True

    def revert(self, snapshot: dict) -> None:
        self.reverts += 1
        if self.raises:
            raise AdapterError(f"{self.name} revert boom")
        self.last_revert_snapshot = snapshot
        self._applied = False

    def verify(self, expected: Literal["on", "off"]) -> bool:
        on = self._verify_on or self._applied
        return on if expected == "on" else not on


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """The orchestrator's own three files, derived from the injected Paths."""
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        state=paths.nightpanel / "nightpanel-state.json",
        active=paths.nightpanel / "nightpanel-active",
        command=paths.nightpanel / "np-command.json",
    )


def _orchestrator(env, adapters: list[Adapter]) -> orch.NightpanelOrchestrator:
    return orch.NightpanelOrchestrator(NIGHTPANEL, adapters, paths=env.paths, runner=env.runner)


def test_apply_happy_path_marks_active(env) -> None:
    a = FakeAdapter("a")
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])

    outcomes = o.apply()

    assert outcomes == {"a": True, "b": True}
    assert env.active.exists(), "ACTIVE_FILE should be marked on success"
    assert a.snapshots_taken == 1 and b.snapshots_taken == 1
    assert a.applies == 1 and b.applies == 1


def test_apply_all_fail_does_not_mark_active(env) -> None:
    """If every adapter raises, ACTIVE_FILE must NOT be touched. State
    machine should report 'off' so a follow-up apply isn't misled into
    capturing the failed-attempt world as a baseline."""
    a = FakeAdapter("a", raises=True)
    b = FakeAdapter("b", raises=True)
    o = _orchestrator(env, [a, b])

    outcomes = o.apply()

    assert outcomes == {"a": False, "b": False}
    assert not env.active.exists(), "ACTIVE_FILE must not be marked when every adapter failed"


def test_apply_partial_success_marks_active(env) -> None:
    """≥1 success is sufficient to call ourselves 'on' when the failed adapter is
    not stateful — it has no baseline it owes anyone, so a partial apply is still
    an apply that can be put back."""
    a = FakeAdapter("a", raises=True, stateful=False)
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])

    outcomes = o.apply()

    assert outcomes == {"a": False, "b": True}
    assert env.active.exists()


def test_stateful_failure_blocks_active_even_with_other_successes(env) -> None:
    """The other side of the same rule: a stateful adapter that failed has a
    baseline it cannot put back, so marking active would make the state machine
    claim 'on' for a world revert cannot restore."""
    a = FakeAdapter("a", raises=True)
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])

    outcomes = o.apply()

    assert outcomes == {"a": False, "b": True}
    assert not env.active.exists(), "a stateful failure must not mark ACTIVE_FILE"


def test_apply_refuses_a_stateful_adapter_with_no_baseline(env) -> None:
    """A half-collected snapshot is not a baseline: the adapter is skipped rather
    than applied, so nothing is marked active when it is the only one."""
    a = FakeAdapter("a", raises=True)  # raises on snapshot, not on apply
    o = _orchestrator(env, [a])

    outcomes = o.apply()

    assert outcomes == {"a": False}
    assert a.applies == 0, "an adapter with no baseline must not be applied"
    assert not env.active.exists()


def test_snapshot_guard_preserves_baseline_when_world_already_on(env) -> None:
    """ACTIVE_FILE missing but verify('on') returns True for some adapter
    means a prior apply landed partially. Re-applying must NOT overwrite
    the existing snapshot with the post-apply state — that was the
    snapshot-corruption bug from 2026-05-25."""
    # First apply captures a baseline + leaves adapters in 'on' state.
    a = FakeAdapter("a")
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])
    o.apply()
    original_state_text = env.state.read_text()
    assert a.snapshots_taken == 1

    # Simulate the failure mode: ACTIVE_FILE goes missing while world stays on.
    env.active.unlink()
    assert a.verify("on") is True  # the world is still on

    # Re-apply should detect this and refuse to re-snapshot.
    o.apply()

    # Snapshot file unchanged.
    assert env.state.read_text() == original_state_text
    # And only the original snapshot was ever taken.
    assert a.snapshots_taken == 1, "snapshot must not be re-taken when world looks on"
    assert b.snapshots_taken == 1


def test_snapshot_taken_fresh_when_world_off(env) -> None:
    """Negative of the guard: if nothing looks on, we DO snapshot fresh."""
    a = FakeAdapter("a")
    o = _orchestrator(env, [a])

    o.apply()
    o.revert()
    # State file should still exist (we don't delete it on revert).
    assert env.state.exists()
    snapshot_count_after_first_cycle = a.snapshots_taken

    # World is off (revert just ran). Apply again — should re-snapshot.
    o.apply()
    assert a.snapshots_taken == snapshot_count_after_first_cycle + 1


# ── revert restores per-adapter snapshots ────────────────────────────────


def test_revert_passes_saved_snapshot_to_each_adapter(env) -> None:
    a = FakeAdapter("a")
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])

    o.apply()
    o.revert()

    # Each adapter's saved snapshot (namespaced by name) is handed back to it.
    assert a.last_revert_snapshot == {"name": "a", "captured": True}
    assert b.last_revert_snapshot == {"name": "b", "captured": True}
    assert a.reverts == 1 and b.reverts == 1
    assert not env.active.exists(), "revert must clear ACTIVE_FILE"


def test_revert_with_missing_state_passes_empty_dict(env) -> None:
    """No state file (or unknown adapter) → adapter gets {} rather than KeyError."""
    a = FakeAdapter("a")
    o = _orchestrator(env, [a])
    o.revert()  # never applied → no state file
    assert a.last_revert_snapshot == {}


def test_revert_continues_after_one_adapter_raises(env) -> None:
    """A failing revert is recorded as an outcome, not propagated: the other
    adapters still get put back. ACTIVE_FILE is already gone by then, so the
    failed adapter's outcome is what the caller sees."""
    a = FakeAdapter("a", raises=True)
    b = FakeAdapter("b")
    o = _orchestrator(env, [a, b])

    o.apply()
    outcomes = o.revert()  # must not propagate

    assert outcomes == {"a": False, "b": True}
    assert b.reverts == 1, "b should still revert even though a raised"
    assert not env.active.exists()


# ── verify() aggregates per-adapter state ────────────────────────────────


def test_verify_returns_dict_for_active_adapters(env) -> None:
    a = FakeAdapter("a", verify_on=True)
    b = FakeAdapter("b", verify_on=False)
    o = _orchestrator(env, [a, b])
    assert o.verify("on") == {"a": True, "b": False}
    assert o.verify("off") == {"a": False, "b": True}


def test_uninstalled_adapters_are_skipped(env) -> None:
    a = FakeAdapter("a")
    b = FakeAdapter("b")
    b._installed = False
    o = _orchestrator(env, [a, b])

    outcomes = o.apply()
    assert outcomes == {"a": True}, "uninstalled adapter must not appear in outcomes"
    assert b.applies == 0 and b.snapshots_taken == 0


# ── brightness updates: gated on ACTIVE_FILE + rate-limited ──────────────


def test_update_brightness_noops_when_inactive(env) -> None:
    o = _orchestrator(env, [FakeAdapter("a")])
    # ACTIVE_FILE absent → no command written (nothing is listening).
    o.update_brightness(0.8)
    assert not env.command.exists()


def test_update_brightness_writes_when_active(env) -> None:
    env.active.parent.mkdir(parents=True, exist_ok=True)
    env.active.touch()
    o = _orchestrator(env, [FakeAdapter("a")])

    o.update_brightness(0.8)
    assert json.loads(env.command.read_text()) == {"action": "apply", "brightness": 0.8}


def test_update_brightness_rate_limited(env) -> None:
    env.active.parent.mkdir(parents=True, exist_ok=True)
    env.active.touch()
    o = _orchestrator(env, [FakeAdapter("a")])

    o.update_brightness(0.8)
    o.update_brightness(0.2)  # within the 0.1s window → suppressed
    assert json.loads(env.command.read_text())["brightness"] == 0.8


def test_video_brightness_uses_independent_rate_limit(env) -> None:
    """A drag on the page-brightness slider must not suppress the video
    slider — they keep separate timestamps."""
    env.active.parent.mkdir(parents=True, exist_ok=True)
    env.active.touch()
    o = _orchestrator(env, [FakeAdapter("a")])

    o.update_brightness(0.8)
    o.update_video_brightness(0.3)  # different timestamp → not suppressed
    assert json.loads(env.command.read_text()) == {
        "action": "apply",
        "videoBrightness": 0.3,
    }


# ── the orchestrator and its adapters must agree on the paths ────────────


def test_orchestrator_and_adapters_resolve_the_same_command_file(env) -> None:
    """The import-time constants could disagree with the adapters about where the
    config dir is (NP_CONFIG_DIR, XDG_CONFIG_HOME). Under injection there is one
    answer, and the Firefox adapter and the orchestrator have to give it."""
    o = _orchestrator(env, [])
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)

    assert o._np_command == adapter._command_file == env.command
    assert o._state_path == env.paths.nightpanel / "nightpanel-state.json"
    assert o._active_file == env.active


# ── install_bridge consent gate ──────────────────────────────────────────


def test_install_bridge_requires_consent(env) -> None:
    o = _orchestrator(env, [FakeAdapter("a")])
    with pytest.raises(orch.ConsentRequired):
        o.install_bridge()  # confirmed defaults to False


def test_is_active_tracks_active_file(env) -> None:
    """is_active() is the single source of truth for 'should we resume the
    system theme' — gates launch-time apply so opening the config window
    doesn't flip the session when nightpanel is off."""
    o = _orchestrator(env, [FakeAdapter("a")])
    assert o.is_active() is False
    o.apply()
    assert o.is_active() is True
    o.revert()
    assert o.is_active() is False


def test_load_state_returns_empty_on_corrupt_file(env) -> None:
    env.state.parent.mkdir(parents=True, exist_ok=True)
    env.state.write_text("{ broken json")
    o = _orchestrator(env, [FakeAdapter("a")])
    assert o._load_state() == {}

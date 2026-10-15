# SPDX-License-Identifier: GPL-3.0-or-later
"""TmuxAdapter — overlay write + source-file flip + status-style probe.

The adapter applies by writing a palette overlay to
``~/.config/nightpanel/themes/tmux-nightpanel.conf`` and sourcing it into the
running server; it reverts by putting back the values it captured. verify()
probes ``status-style`` for the palette's status background.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): there is nothing to patch, and the
assertions are derived from the palette instead of from one palette's hex.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError, AdapterNotLive
from nightpanel.adapters.tmux import _OPTIONS, TmuxAdapter
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    return SimpleNamespace(
        paths=paths,
        overlay=paths.nightpanel / "themes" / "tmux-nightpanel.conf",
        runner=runner,
    )


def test_installed_follows_which(paths: Paths, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/tmux")
    assert TmuxAdapter(paths=paths).installed() is True
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert TmuxAdapter(paths=paths).installed() is False


def test_apply_writes_overlay_and_sources_it(env):
    TmuxAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    assert env.overlay.exists()
    body = env.overlay.read_text()
    assert "status-style" in body
    # Derived from the palette, not from the hex that happened to be right for
    # one palette — the battery enforces the same rule on the adapter source.
    assert f"bg={NIGHTPANEL.bg_header}" in body
    # The running server was told to source the overlay we just wrote.
    assert ["tmux", "source-file", str(env.overlay)] in env.runner.calls


def test_revert_refuses_an_empty_snapshot(env):
    """Re-sourcing ~/.tmux.conf is a guess, and it overwrites options the user
    set by hand. The adapter refuses instead, so this test asserts the refusal."""
    adapter = TmuxAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="tmux"):
        adapter.revert({})
    assert not env.runner.calls


def test_revert_restores_what_snapshot_captured(env):
    live = RecordingRunner(prefix={("tmux", "show", "-gv"): "user-value"})
    adapter = TmuxAdapter(paths=env.paths, runner=live)
    snapshot = adapter.snapshot()
    assert snapshot and all(v == "user-value" for v in snapshot.values())

    adapter.apply(NIGHTPANEL)
    live.calls.clear()
    adapter.revert(snapshot)
    assert [["tmux", "set", "-g", opt, "user-value"] for opt in _OPTIONS] == live.calls


def test_snapshot_records_none_for_options_with_no_live_server(env):
    """`None` is the honest answer for "nothing running to capture"; `{}` is not."""
    no_server = RecordingRunner(no_session=True)
    snapshot = TmuxAdapter(paths=env.paths, runner=no_server).snapshot()
    assert set(snapshot) == set(_OPTIONS)
    assert all(v is None for v in snapshot.values())


def test_verify_on_when_status_style_matches_the_palette(env):
    env.runner.answers[("tmux", "show", "-gv", "status-style")] = (
        f"fg=#7DB890,bg={NIGHTPANEL.bg_header}"
    )
    adapter = TmuxAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False


def test_verify_off_when_status_style_is_not_the_palette(env):
    env.runner.answers[("tmux", "show", "-gv", "status-style")] = "fg=colour250,bg=colour236"
    adapter = TmuxAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("off") is True
    assert adapter.verify("on") is False


def test_verify_off_on_nonzero_returncode(env):
    env.runner.fail = {("tmux", "show", "-gv", "status-style")}
    env.runner.answers[("tmux", "show", "-gv", "status-style")] = f"bg={NIGHTPANEL.bg_header}"
    assert TmuxAdapter(paths=env.paths, runner=env.runner).verify("on") is False


def test_apply_raises_when_the_overlay_cannot_be_written(env):
    """A write failure is a failure, not a warning: the orchestrator's accounting
    and ACTIVE_FILE depend on the adapter saying so."""
    env.overlay.mkdir(parents=True, exist_ok=True)  # the target is a directory
    with pytest.raises(AdapterError, match="tmux"):
        TmuxAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)


def test_a_failed_source_is_an_outcome_not_a_success(env):
    """No server is normal, but it is still an outcome the caller can see —
    `run_optional` reports it instead of swallowing it."""
    failing = RecordingRunner(fail_all=True)
    adapter = TmuxAdapter(paths=env.paths, runner=failing)
    with pytest.raises(AdapterNotLive, match="no tmux server"):
        adapter.apply(NIGHTPANEL)
    assert env.overlay.exists(), "the file half still landed; only the live half did not"

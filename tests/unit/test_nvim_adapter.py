# SPDX-License-Identifier: GPL-3.0-or-later
"""NvimAdapter — after/plugin override create/remove + colorscheme restore.

apply() drops ``after/plugin/nightpanel_active.lua`` (which forces the
nightpanel colorscheme) and nudges any live ``--server`` sessions; revert()
removes the file and restores the colorscheme captured at snapshot time.
verify() keys off the override file's content, not its mere presence.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the live session is a socket file
under ``paths.runtime`` and the answers come from the RecordingRunner, so
nothing is patched. The empty-snapshot test asserts the refusal instead of the
old hardcoded-default fallback — restoring "tokyonight" on a guess is the same
class of damage as guessing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.nvim import NvimAdapter
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

_NP_CS = "nightpanel"
_DEFAULT_CS = "tokyonight"


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        after=paths.config / "nvim" / "after" / "plugin" / "nightpanel_active.lua",
        sock=paths.runtime / "nvim.4242.0",
    )


def _make_live(env, colorscheme: str) -> None:
    """A live session is a socket the adapter can find plus an answer."""
    env.sock.touch()
    env.runner.answers[("nvim", "--server", str(env.sock), "--remote-expr", "g:colors_name")] = (
        colorscheme
    )


def test_installed_follows_which(paths: Paths, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/nvim")
    assert NvimAdapter(paths=paths).installed() is True
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert NvimAdapter(paths=paths).installed() is False


def test_apply_writes_override_and_sends_colorscheme(env):
    _make_live(env, _DEFAULT_CS)
    NvimAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    assert env.after.exists()
    assert f"colorscheme {_NP_CS}" in env.after.read_text()
    # The live session was told to switch.
    assert any(
        cmd[:3] == ["nvim", "--server", str(env.sock)] and f"colorscheme {_NP_CS}" in cmd[-1]
        for cmd in env.runner.calls
    )


def test_verify_tracks_override_file(env):
    adapter = NvimAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("off") is True
    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False


def test_snapshot_defaults_when_no_live_session(env):
    """No socket is the honest "nothing running to capture" state; the file
    half is still captured, and the colorscheme falls back to the default."""
    dead = RecordingRunner(no_session=True)
    snap = NvimAdapter(paths=env.paths, runner=dead).snapshot()
    assert snap["colorscheme"] == _DEFAULT_CS
    assert snap["file"]["existed"] is False


def test_snapshot_captures_live_colorscheme(env):
    _make_live(env, "gruvbox")
    snap = NvimAdapter(paths=env.paths, runner=env.runner).snapshot()
    assert snap["colorscheme"] == "gruvbox"


def test_revert_removes_override_and_restores_previous(env):
    _make_live(env, "gruvbox")
    adapter = NvimAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()
    adapter.apply(NIGHTPANEL)
    env.runner.calls.clear()

    adapter.revert(snap)
    assert not env.after.exists()
    assert adapter.verify("off") is True
    assert any(
        cmd[:3] == ["nvim", "--server", str(env.sock)] and "colorscheme gruvbox" in cmd[-1]
        for cmd in env.runner.calls
    )


def test_revert_refuses_an_empty_snapshot(env):
    """The old fallback re-sent a hardcoded "tokyonight" on an empty snapshot,
    overwriting whatever the user actually had. The adapter refuses instead."""
    adapter = NvimAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})

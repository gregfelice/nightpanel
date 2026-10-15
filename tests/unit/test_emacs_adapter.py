# SPDX-License-Identifier: GPL-3.0-or-later
"""EmacsAdapter — sentinel + emacsclient round-trip.

apply() drops a sentinel (the only signal that survives across daemon
restarts) and tells any live daemon to load the installed nightpanel theme.
It no longer writes ``nightpanel-theme.el``: that ships as its own package.
revert() removes the sentinel and disables the theme. verify() keys purely
off the sentinel.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py). Two tests changed shape: the
daemon-answers-but-cannot-load case is now an ``AdapterError`` (a real
failure), and the no-daemon case is ``AdapterNotLive`` — both used to be
log-and-return, which recorded a success to the orchestrator while nothing
changed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError, AdapterNotLive
from nightpanel.adapters.emacs import EmacsAdapter
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

_LOAD_FORM = "(condition-case nil (progn (load-theme (quote nightpanel) t) t) (error nil))"
_DISABLE_FORM = "(disable-theme (quote nightpanel))"


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """A live daemon that has nightpanel-theme installed and answers t."""
    runner.answers = {
        ("emacsclient", "-e", "custom-enabled-themes"): "(doom-one)",
        ("emacsclient", "-e", _LOAD_FORM): "t",
    }
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        sentinel=paths.nightpanel / "emacs-active",
    )


def test_installed_follows_which(paths: Paths, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/emacs")
    assert EmacsAdapter(paths=paths).installed() is True
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert EmacsAdapter(paths=paths).installed() is False


def test_apply_writes_sentinel_and_loads(env):
    EmacsAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    assert env.sentinel.exists()
    # A daemon was asked to load the theme.
    assert any("load-theme (quote nightpanel)" in " ".join(cmd) for cmd in env.runner.calls)


def test_apply_does_not_write_a_theme_file(env):
    # The theme is an installed package; rendering it here is what caused the
    # two copies to drift, so apply() must not put any .el on disk.
    EmacsAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)
    assert not list(env.paths.home.rglob("*.el"))


def test_apply_raises_when_the_daemon_cannot_load_the_theme(env, monkeypatch):
    """condition-case swallowed a load failure -> nil, not t. A daemon that
    answers but cannot load the theme is a real failure, not a warning: the
    old log-and-return recorded a success while nothing changed."""
    env.runner.answers[("emacsclient", "-e", _LOAD_FORM)] = "nil"
    with pytest.raises(AdapterError, match="not loaded"):
        EmacsAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)
    # The sentinel half still landed — only the live half failed.
    assert env.sentinel.exists()


def test_apply_reports_not_live_when_no_daemon_is_running(env):
    """No daemon is the common case and not a failure, but it is still an
    outcome the caller can see — `run_optional` reports it instead of
    returning quietly."""
    dead = RecordingRunner(
        answers={("emacsclient", "-e", "custom-enabled-themes"): "(doom-one)"},
        no_session=True,
    )
    adapter = EmacsAdapter(paths=env.paths, runner=dead)
    with pytest.raises(AdapterNotLive, match="no emacs daemon"):
        adapter.apply(NIGHTPANEL)
    assert env.sentinel.exists(), "the sentinel half still landed; only the live half did not"


def test_verify_tracks_sentinel(env):
    adapter = EmacsAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("off") is True  # no sentinel yet
    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False


def test_snapshot_captures_enabled_themes(env):
    snap = EmacsAdapter(paths=env.paths, runner=env.runner).snapshot()
    assert snap["enabled_themes"] == "(doom-one)"
    # The sentinel is declared as its own, but a file already standing there is user
    # state, so the capture covers it too. Nothing was there in this fixture.
    assert snap["own"][str(env.sentinel)]["existed"] is False


def test_revert_removes_sentinel_and_disables(env):
    adapter = EmacsAdapter(paths=env.paths, runner=env.runner)
    adapter.apply(NIGHTPANEL)
    assert env.sentinel.exists()

    adapter.revert({"enabled_themes": "(doom-one)"})
    assert not env.sentinel.exists()
    assert adapter.verify("off") is True
    assert any("disable-theme (quote nightpanel)" in " ".join(cmd) for cmd in env.runner.calls)


def test_apply_raises_when_the_sentinel_cannot_be_written(env):
    """A write failure is a failure, not a warning: the orchestrator's
    accounting depends on the adapter saying so."""
    env.sentinel.mkdir(parents=True)  # the target is a directory
    with pytest.raises(AdapterError, match="emacs"):
        EmacsAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)


def test_revert_is_safe_with_empty_snapshot(env):
    """Emacs is the one adapter that honestly declares `stateful = False`: its
    only state is a live daemon's theme list, which cannot be put back
    byte-for-byte. So an empty snapshot is not a guess here — the sentinel is
    its own artifact and the daemon command is best-effort."""
    adapter = EmacsAdapter(paths=env.paths, runner=env.runner)
    adapter.revert({})  # must not crash
    assert not env.sentinel.exists()
    assert any("disable-theme (quote nightpanel)" in " ".join(cmd) for cmd in env.runner.calls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ClaudeCodeAdapter — focus on stale-session detection.

The adapter's theme-flip path is exercised end-to-end by orchestrator
tests; these tests target the ``_find_stale_claude_pids`` helper that
surfaces the design gap where a claude session pre-dating
nightpanel-active keeps its inherited COLORTERM and bypasses the
NP-tinted ANSI palette. The gap is reported log-only — a desktop
notification was removed because it re-fired on every toggle-on for any
long-lived pre-wrapper session (spamming the tray and lock screen).

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the settings file, the wrapper and
the versions dir are all derived from the same ``Paths`` the adapter resolves, so
the fake /proc entries name the same tree the adapter is looking for. The caplog
assertions stay — that warning is informational reporting, not a swallowed
failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.claude_code import ClaudeCodeAdapter
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """The adapter's three locations, all under the fixture, plus a fake /proc.

    ``versions`` is the tree the cmdline scan matches against, so the fake pid
    entries are written with the same path the adapter resolves — nothing is
    patched for the scan to agree.
    """
    versions = paths.data / "claude" / "versions"
    versions.mkdir(parents=True)
    (versions / "2.1.152").write_text("#!/bin/sh\n")

    # The user's existing config, with a key nightpanel must not drop.
    settings = paths.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{\n  "theme": "light",\n  "verbose": true\n}\n')

    proc = paths.home / "proc"
    proc.mkdir()

    return SimpleNamespace(
        paths=paths,
        runner=runner,
        settings=settings,
        wrapper=paths.bin / "claude",
        versions=versions,
        proc=proc,
    )


def _pid(env, pid: int, cmdline: bytes, environ: bytes) -> Path:
    """One fake /proc entry: cmdline and environ as NUL-separated payloads."""
    d = env.proc / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(cmdline)
    (d / "environ").write_bytes(environ)
    return d


def test_find_stale_finds_claude_with_colorterm(env) -> None:
    _pid(
        env,
        4242,
        f"{env.versions}/2.1.152\x00--dangerously-skip-permissions\x00".encode(),
        b"COLORTERM=truecolor\x00TERM=alacritty\x00PATH=/usr/bin\x00",
    )

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert stale == [4242]


def test_find_stale_skips_claude_without_colorterm(env) -> None:
    """A claude launched after NP was active has COLORTERM stripped —
    it's already the cleanly-themed case, not stale."""
    _pid(
        env,
        4243,
        f"{env.versions}/2.1.152\x00".encode(),
        b"TERM=alacritty\x00PATH=/usr/bin\x00",
    )

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert stale == []


def test_find_stale_ignores_non_claude_processes(env) -> None:
    _pid(env, 5000, b"/usr/bin/python3\x00script.py\x00", b"COLORTERM=truecolor\x00TERM=alacritty\x00")
    _pid(env, 5001, b"/usr/bin/firefox\x00", b"COLORTERM=truecolor\x00")

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert stale == []


def test_find_stale_handles_unreadable_entries(env) -> None:
    """Permission errors on a pid dir (e.g. another user's process)
    must not abort the scan — the loop should continue."""
    (env.proc / "9999").mkdir()  # no cmdline/environ files → OSError on read → skipped
    _pid(env, 4242, f"{env.versions}/2.1.152\x00".encode(), b"COLORTERM=truecolor\x00")

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert stale == [4242]


def test_find_stale_skips_non_numeric_dirs(env) -> None:
    (env.proc / "self").mkdir()
    (env.proc / "meminfo").write_text("noise")
    _pid(env, 4242, f"{env.versions}/2.1.152\x00".encode(), b"COLORTERM=truecolor\x00")

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert stale == [4242]


def test_find_stale_finds_multiple(env) -> None:
    for pid in (4242, 4243, 4244):
        _pid(env, pid, f"{env.versions}/2.1.152\x00".encode(), b"COLORTERM=truecolor\x00")

    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc
    )
    assert sorted(stale) == [4242, 4243, 4244]


def test_find_stale_returns_empty_when_proc_unreadable(env) -> None:
    """If /proc itself can't be enumerated, return [] rather than raise."""
    stale = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)._find_stale_claude_pids(
        proc_root=env.proc / "nope"
    )
    assert stale == []


def test_wrapper_script_names_the_injected_tree(env) -> None:
    """The wrapper is generated from this instance's paths.

    A module-level f-string baked the developer's home into every generated
    script, so a wrapper written on one machine pointed at another machine's
    tree. Under injection the script can only name the fixture.
    """
    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)
    script = adapter._wrapper_script()

    assert str(env.versions) in script
    assert str(env.paths.nightpanel / "nightpanel-active") in script
    assert str(Path.home()) not in script, "the generated script still names a real home"


def test_apply_logs_when_stale_present(env, caplog) -> None:
    """apply() detects stale claudes and reports them log-only.

    No desktop notification is fired — the journal warning is the durable
    signal (a popup re-fired on every toggle, spamming tray/lock screen).
    """
    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)

    with (
        patch.object(adapter, "_find_stale_claude_pids", return_value=[1234, 5678]),
        caplog.at_level("WARNING"),
    ):
        adapter.apply(NIGHTPANEL)

    # The warning names the count so the journal records the scope.
    assert "2 claude session(s)" in caplog.text
    assert "1234" in caplog.text
    # And the apply itself still landed on the fixture's tree.
    assert env.wrapper.exists(), "the wrapper was not written under the injected paths"
    assert json.loads(env.settings.read_text())["theme"] == "dark"
    assert "verbose" in env.settings.read_text(), "apply dropped a key it does not own"


def test_apply_silent_when_no_stale(env, caplog) -> None:
    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)

    with (
        patch.object(adapter, "_find_stale_claude_pids", return_value=[]),
        caplog.at_level("WARNING"),
    ):
        adapter.apply(NIGHTPANEL)

    assert "claude session(s)" not in caplog.text
    assert env.wrapper.exists()


def test_revert_refuses_an_empty_snapshot(env) -> None:
    """The old version captured the wrapper link and never used it, so toggling
    off left the wrapper behind. Under the contract an empty snapshot is a guess
    and the adapter refuses it."""
    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="claude_code"):
        adapter.revert({})


def test_revert_restores_the_installer_symlink_as_a_link(env) -> None:
    """The wrapper replaces the installer's symlink; "put it back" has to mean
    the link itself, not the content behind it as a regular file."""
    target = env.versions / "2.1.152"
    env.paths.bin.mkdir(parents=True, exist_ok=True)
    env.wrapper.symlink_to(target)  # the installer's link: ~/.local/bin/claude → versions/…
    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)

    snapshot = adapter.snapshot()
    adapter.apply(NIGHTPANEL)
    adapter.revert(snapshot)

    assert env.wrapper.is_symlink(), "revert wrote a file where the installer's link was"
    assert env.wrapper.readlink() == target


def test_apply_replaces_the_link_instead_of_writing_through_it(env) -> None:
    target = env.versions / "2.1.152"
    original = "#!/bin/sh\nreal binary\n"
    target.write_text(original)
    env.paths.bin.mkdir(parents=True, exist_ok=True)
    env.wrapper.symlink_to(target)

    adapter = ClaudeCodeAdapter(paths=env.paths, runner=env.runner)
    adapter.apply(NIGHTPANEL)

    assert env.wrapper.is_symlink() is False, "the link is still there"
    assert target.read_text() == original, "the real binary was overwritten through the link"

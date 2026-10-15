# SPDX-License-Identifier: GPL-3.0-or-later
"""GwsAdapter — theme line flip in gws's todo.state, preserving other lines.

gws persists its theme as a ``theme:<Name>`` line in ``~/.gws/todo.state``,
alongside collapse markers (cat:/proj:/task:). The adapter must flip only the
theme line on apply, restore the prior theme on revert, and clean up the file
entirely if it created it.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the state file lands under the
fixture, and the snapshot assertions follow the capture shape the adapter now
records (existence + content, not just the theme value).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.gws import GwsAdapter, _parse, _serialize
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        state=paths.home / ".gws" / "todo.state",
    )


# ── pure parse/serialize ────────────────────────────────────────────────


def test_parse_splits_theme_from_other_lines():
    theme, others = _parse("theme:Dracula\ncat:work:1\nproj:home:0\n")
    assert theme == "Dracula"
    assert others == ["cat:work:1", "proj:home:0"]


def test_parse_no_theme_line():
    theme, others = _parse("cat:work:1\n")
    assert theme is None
    assert others == ["cat:work:1"]


def test_serialize_round_trip_preserves_order():
    text = _serialize("Nightpanel", ["cat:work:1", "proj:home:0"])
    assert text == "theme:Nightpanel\ncat:work:1\nproj:home:0\n"


def test_serialize_empty_is_empty_string():
    assert _serialize(None, []) == ""


# ── adapter behavior ────────────────────────────────────────────────────


def test_installed_when_state_dir_exists(env, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    env.state.parent.mkdir(parents=True)
    assert GwsAdapter(paths=env.paths).installed() is True


def test_apply_sets_theme_and_preserves_other_lines(env):
    env.state.parent.mkdir(parents=True)
    env.state.write_text("theme:Dracula\ncat:work:1\n")

    GwsAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    theme, others = _parse(env.state.read_text())
    assert theme == "Nightpanel"
    assert others == ["cat:work:1"]


def test_verify_tracks_theme(env):
    env.state.parent.mkdir(parents=True)
    adapter = GwsAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("off") is True  # no file
    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False


def test_snapshot_records_prior_theme_and_existence(env):
    env.state.parent.mkdir(parents=True)
    env.state.write_text("theme:Dracula\ncat:work:1\n")
    snap = GwsAdapter(paths=env.paths, runner=env.runner).snapshot()
    assert snap["theme"] == "Dracula"
    assert snap["file"]["existed"] is True
    assert snap["file"]["content"] == "theme:Dracula\ncat:work:1\n"


def test_snapshot_when_no_state_file(env):
    snap = GwsAdapter(paths=env.paths, runner=env.runner).snapshot()
    assert snap["theme"] is None
    assert snap["file"]["existed"] is False


def test_revert_refuses_an_empty_snapshot(env):
    """An empty snapshot cannot tell "the file did not exist before us" from
    "we never looked", and guessing either way can destroy user config."""
    adapter = GwsAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})


def test_revert_restores_prior_theme(env):
    env.state.parent.mkdir(parents=True)
    env.state.write_text("theme:Dracula\ncat:work:1\n")
    adapter = GwsAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()

    adapter.apply(NIGHTPANEL)
    adapter.revert(snap)

    theme, others = _parse(env.state.read_text())
    assert theme == "Dracula"
    assert others == ["cat:work:1"]


def test_revert_drops_file_it_created_when_only_theme_line(env):
    """If gws had no state file and we created one holding only our theme
    line, revert removes the whole file rather than leaving an orphan."""
    env.state.parent.mkdir(parents=True)
    adapter = GwsAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()  # existed=False
    adapter.apply(NIGHTPANEL)
    assert env.state.exists()

    adapter.revert(snap)
    assert not env.state.exists()


def test_revert_strips_only_our_line_when_other_content_appeared(env):
    """File didn't exist before us, but other lines showed up alongside our
    theme; revert must strip only the theme line and keep the rest."""
    env.state.parent.mkdir(parents=True)
    adapter = GwsAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()  # existed=False
    adapter.apply(NIGHTPANEL)
    # Simulate gws adding its own markers while nightpanel was active.
    env.state.write_text("theme:Nightpanel\ncat:work:1\n")

    adapter.revert(snap)
    assert env.state.exists()
    theme, others = _parse(env.state.read_text())
    assert theme is None
    assert others == ["cat:work:1"]

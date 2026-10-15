# SPDX-License-Identifier: GPL-3.0-or-later
"""Alacritty adapter — import-flip round-trip, incl. the multi-line array form.

Regression: the old regex adapter only understood single-line
``import = ["…"]`` and corrupted the multi-line array form on apply/revert,
leaving invalid TOML that alacritty refused to load.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the adapter's files land under the
fixture, and the corrupt-file test asserts the refusal the adapter now makes
instead of the silent overwrite it used to perform.
"""

from __future__ import annotations

import tomllib
from types import SimpleNamespace

import pytest

from nightpanel.adapters.alacritty import AlacrittyAdapter
from nightpanel.adapters.base import AdapterError
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

_MULTILINE = """\
[general]
import = [
    "~/.config/alacritty/themes/themes/ayu_dark.toml"
]

[window]
padding = { x = 12, y = 12 }
"""

_SINGLE_LINE = """\
[general]
import = ["~/.config/alacritty/themes/themes/ayu_dark.toml"]

[font]
size = 12.0
"""


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        cfg=paths.config / "alacritty" / "alacritty.toml",
        theme=paths.config / "alacritty" / "themes" / "themes" / "nightpanel.toml",
    )


def _parses(path):
    """Raises if the file is not valid TOML; returns the parsed dict."""
    return tomllib.load(open(path, "rb"))


@pytest.mark.parametrize("original", [_MULTILINE, _SINGLE_LINE], ids=["multiline", "single"])
def test_apply_revert_round_trip_preserves_import(env, original):
    env.cfg.write_text(original)
    adapter = AlacrittyAdapter(paths=env.paths, runner=env.runner)

    # Orchestrator snapshots BEFORE apply, then reverts with that snapshot.
    snap = adapter.snapshot()
    assert snap["import"] == ["~/.config/alacritty/themes/themes/ayu_dark.toml"]

    adapter.apply(NIGHTPANEL)
    applied = _parses(env.cfg)  # must stay valid TOML — the old bug failed here
    assert applied["general"]["import"] == [str(env.theme)]
    assert adapter.verify("on")

    adapter.revert(snap)
    reverted = _parses(env.cfg)
    assert reverted["general"]["import"] == ["~/.config/alacritty/themes/themes/ayu_dark.toml"]
    assert adapter.verify("off")


def test_revert_with_no_prior_import_removes_key(env):
    env.cfg.write_text("[general]\n\n[window]\npadding = { x = 12, y = 12 }\n")
    adapter = AlacrittyAdapter(paths=env.paths, runner=env.runner)

    snap = adapter.snapshot()
    assert snap["import"] is None

    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on")

    adapter.revert(snap)
    data = _parses(env.cfg)
    assert "import" not in data.get("general", {})
    assert adapter.verify("off")


def test_corrupt_config_refuses_to_read(env):
    """A pre-existing broken file is a real failure, not a silent no-op: the
    old behaviour let apply() overwrite an unreadable file, destroying the
    user's config. The adapter now refuses, so this asserts the refusal."""
    env.cfg.write_text('[general]\n    "orphaned.toml"\n]\n')
    adapter = AlacrittyAdapter(paths=env.paths, runner=env.runner)

    with pytest.raises(AdapterError, match="unreadable"):
        adapter.snapshot()
    with pytest.raises(AdapterError, match="unreadable"):
        adapter.verify("off")


def test_revert_refuses_an_empty_snapshot(env):
    adapter = AlacrittyAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})

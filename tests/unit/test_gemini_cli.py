# SPDX-License-Identifier: GPL-3.0-or-later
"""GeminiCliAdapter — custom-theme write + truecolor-stripping wrapper.

The settings swap and the wrapper are both user state, so the round trip is
provable: snapshot captures both, revert puts both back (the wrapper as the
symlink the installer created, or removes it if it did not exist before us).

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py). The verify test now takes a real
snapshot before apply — the old one called ``revert({"theme": "Default"})``,
which under the contract is a guess the adapter refuses. The wrapper assertion
also proves the script is generated from *this instance's* paths, not a
module-level f-string that baked in the developer's home.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.gemini_cli import _WRAPPER_MARKER, GeminiCliAdapter
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """The fixture root is not pre-created, so the directories the tests write into
    are made here — the same as the invariants seeders do."""
    settings = paths.home / ".gemini" / "settings.json"
    wrapper = paths.bin / "gemini"
    for path in (settings, wrapper):
        path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(paths=paths, runner=runner, settings=settings, wrapper=wrapper)


def test_apply_writes_custom_theme(env):
    """apply() flips ui.theme to 'nightpanel' and writes the customTheme block.

    Mixed-form check: text/status slots use ANSI color names (which
    alacritty's repainted 16-color palette renders exactly under the
    COLORTERM strip) while backgrounds use the palette hex so gemini's
    interpolation math produces predictable derivatives.
    """
    GeminiCliAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    assert env.settings.exists()
    data = json.loads(env.settings.read_text())
    assert data["ui"]["theme"] == "nightpanel"

    theme = data["ui"]["customThemes"]["nightpanel"]
    # Backgrounds clamp to alacritty canvas.
    assert theme["background"]["primary"] == NIGHTPANEL.bg
    assert theme["background"]["diff"]["added"] == NIGHTPANEL.bg
    assert theme["background"]["diff"]["removed"] == NIGHTPANEL.bg
    # Text slots are ANSI names — exact palette match via alacritty.
    assert theme["text"]["primary"] == "green"
    assert theme["text"]["link"] == "greenbright"
    assert theme["text"]["accent"] == "yellow"
    # Hex on text.secondary drives the bg interpolation toward canvas
    # without forcing secondary text to disappear.
    assert theme["text"]["secondary"] == NIGHTPANEL.fg_mid
    assert theme["status"]["error"] == "red"
    assert theme["ui"]["comment"] == "blue"
    assert theme["ui"]["symbol"] == "yellow"
    # DarkGray bypasses interpolation and sets borders directly.
    assert theme["DarkGray"] == NIGHTPANEL.border_d


def test_apply_preserves_other_custom_themes(env):
    """User-defined customThemes (other than 'nightpanel') survive apply."""
    env.settings.write_text(
        json.dumps(
            {
                "ui": {
                    "theme": "user-custom",
                    "customThemes": {
                        "user-custom": {"name": "user-custom"},
                    },
                }
            }
        )
    )
    GeminiCliAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)

    data = json.loads(env.settings.read_text())
    assert data["ui"]["theme"] == "nightpanel"
    assert "user-custom" in data["ui"]["customThemes"]
    assert "nightpanel" in data["ui"]["customThemes"]


def test_revert_restores_previous_theme(env):
    adapter = GeminiCliAdapter(paths=env.paths, runner=env.runner)

    env.settings.write_text(json.dumps({"ui": {"theme": "Dracula"}}))
    snapshot = adapter.snapshot()

    adapter.apply(NIGHTPANEL)
    assert json.loads(env.settings.read_text())["ui"]["theme"] == "nightpanel"

    adapter.revert(snapshot)
    assert json.loads(env.settings.read_text())["ui"]["theme"] == "Dracula"


def test_snapshot_ignores_own_applied_value(env):
    """If the live theme is already our applied 'nightpanel', revert
    should fall back to 'Default' rather than reapplying our own value
    after the customTheme block is gone."""
    adapter = GeminiCliAdapter(paths=env.paths, runner=env.runner)

    env.settings.write_text(json.dumps({"ui": {"theme": "nightpanel"}}))
    snapshot = adapter.snapshot()
    assert snapshot["theme"] is None

    adapter.apply(NIGHTPANEL)
    adapter.revert(snapshot)
    assert json.loads(env.settings.read_text())["ui"]["theme"] == "Default"


def test_verify_tracks_theme_and_wrapper(env):
    adapter = GeminiCliAdapter(paths=env.paths, runner=env.runner)

    # Pre-write the bare marker; full content equality check will fail
    # until apply() rewrites it with the real wrapper script.
    env.wrapper.write_text(_WRAPPER_MARKER)

    assert adapter.verify("on") is False

    snapshot = adapter.snapshot()  # records the marker file + no settings
    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False

    # The round trip puts both halves back: settings removed (it did not
    # exist before us), wrapper restored byte-for-byte as the marker file.
    adapter.revert(snapshot)
    assert not env.settings.exists()
    assert env.wrapper.read_text() == _WRAPPER_MARKER
    assert adapter.verify("on") is False
    assert adapter.verify("off") is True


def test_revert_refuses_an_empty_snapshot(env):
    adapter = GeminiCliAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})


def test_wrapper_strips_colorterm_behind_this_instance_sentinel(env):
    """The installed wrapper script must strip COLORTERM behind the
    nightpanel-active sentinel — same trick as claude_code. The sentinel path
    in the script is derived from the injected Paths, which is what makes the
    wrapper correct on a machine whose config lives outside the default."""
    GeminiCliAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)
    script = env.wrapper.read_text()
    assert "env -u COLORTERM" in script
    assert str(env.paths.nightpanel / "nightpanel-active") in script

# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the GNOME adapter's gsettings side effects.

Contract (configurable, default black — the Saab "pure black" design):

  - apply(): sets dark color-scheme + GTK CSS, and by DEFAULT blacks out the
    desktop (clears picture-uri + picture-uri-dark, paints primary-color the
    palette canvas, picture-options=none).
  - opt-out: a ``<config-dir>/keep-wallpaper`` sentinel makes apply() leave the
    background untouched (for users who don't want their wallpaper nuked).
  - revert(): always restores the background from the snapshot, so toggle-off
    puts the user's wallpaper back regardless of which mode applied.

Migrated from patching the adapter's private helpers to injected
``Paths``/``Runner`` (see tests/conftest.py): gsettings calls are observed as
recorded commands, the keep-wallpaper sentinel is a real file under the
fixture, and the CSS round trip is asserted as the byte-for-byte restore the
contract now requires.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.gnome import GnomeAdapter
from nightpanel.palette import NIGHTPANEL, Palette
from nightpanel.paths import Paths
from nightpanel.renderers.gtk import START_SENTINEL
from nightpanel.runner import RecordingRunner

_BG_SCHEMA = "org.gnome.desktop.background"
_INTERFACE_SCHEMA = "org.gnome.desktop.interface"


@pytest.fixture
def palette():
    return Palette()


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        gtk4=paths.config / "gtk-4.0" / "gtk.css",
        gtk3=paths.config / "gtk-3.0" / "gtk.css",
    )


def _sets(runner: RecordingRunner) -> set[tuple[str, str, str]]:
    """Every ``gsettings set`` the adapter issued, as (schema, key, value)."""
    return {(cmd[2], cmd[3], cmd[4]) for cmd in runner.calls if cmd[1] == "set"}


def test_apply_blacks_out_wallpaper_by_default(env, palette):
    GnomeAdapter(paths=env.paths, runner=env.runner).apply(palette)
    bg = {(k, v) for (s, k, v) in _sets(env.runner) if s == _BG_SCHEMA}
    assert ("picture-uri", "") in bg
    assert ("picture-uri-dark", "") in bg
    assert ("primary-color", palette.bg) in bg
    assert ("picture-options", "none") in bg


def test_apply_keeps_wallpaper_when_opted_out(env, palette):
    (env.paths.nightpanel / "keep-wallpaper").touch()
    GnomeAdapter(paths=env.paths, runner=env.runner).apply(palette)
    bg_keys = [(s, k, v) for (s, k, v) in _sets(env.runner) if s == _BG_SCHEMA]
    assert bg_keys == [], f"keep-wallpaper opt-out must leave the background alone, got {bg_keys}"


def test_apply_still_sets_dark_color_scheme(env, palette):
    GnomeAdapter(paths=env.paths, runner=env.runner).apply(palette)
    assert (_INTERFACE_SCHEMA, "color-scheme", "prefer-dark") in _sets(env.runner)


def test_apply_writes_gtk_css_and_bounces_nautilus(env, palette):
    """The CSS half and the Nautilus bounce are real effects, so they are
    asserted as effects: the sentinel-wrapped CSS lands at both config paths,
    and the running Nautilus is asked to quit so it re-parses it."""
    GnomeAdapter(paths=env.paths, runner=env.runner).apply(palette)

    for path in (env.gtk4, env.gtk3):
        assert path.exists(), f"{path} was not written"
        css = path.read_text()
        assert START_SENTINEL in css
        assert palette.bg in css, "the CSS must be rendered from the palette, not one hex"

    assert ["gapplication", "action", "org.gnome.Nautilus", "kill"] in env.runner.calls


def test_revert_restores_wallpaper_and_css_from_snapshot(env):
    """The rescue path: a user toggled on under the old build has their real
    wallpaper in the snapshot; revert must put it back — and put the CSS files
    back byte-for-byte, which the old version never did."""
    user_css = "user css — nightpanel must put this back\n"
    env.gtk4.parent.mkdir(parents=True, exist_ok=True)
    env.gtk3.parent.mkdir(parents=True, exist_ok=True)
    env.gtk4.write_text(user_css)
    env.gtk3.write_text(user_css)
    env.runner.prefix[("gsettings", "get")] = "'user-value'"

    adapter = GnomeAdapter(paths=env.paths, runner=env.runner)
    snapshot = adapter.snapshot()
    adapter.apply(NIGHTPANEL)
    env.runner.calls.clear()
    adapter.revert(snapshot)

    sets = _sets(env.runner)
    assert (_BG_SCHEMA, "picture-uri", "user-value") in sets
    assert (_BG_SCHEMA, "picture-uri-dark", "user-value") in sets
    assert (_BG_SCHEMA, "primary-color", "user-value") in sets
    assert (_BG_SCHEMA, "picture-options", "user-value") in sets
    assert (_INTERFACE_SCHEMA, "color-scheme", "user-value") in sets
    assert env.gtk4.read_text() == user_css
    assert env.gtk3.read_text() == user_css


def test_revert_refuses_an_empty_snapshot(env):
    adapter = GnomeAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})


def test_snapshot_still_captures_background_keys(env):
    """revert can only rescue if snapshot keeps recording the bg — guard that
    the keys weren't dropped along with the apply-time writes."""
    env.runner.prefix[("gsettings", "get")] = "'user-value'"
    snap = GnomeAdapter(paths=env.paths, runner=env.runner).snapshot()
    for key in ("color_scheme", "bg_uri", "bg_uri_dark", "bg_color", "bg_options"):
        assert key in snap
    assert snap["bg_uri"] == "user-value"


def test_verify_keys_off_color_scheme_and_sentinel(env):
    """verify("on") needs both the dark scheme and the sentinel-wrapped CSS —
    one alone is not nightpanel."""
    env.runner.prefix[("gsettings", "get")] = "'prefer-dark'"
    adapter = GnomeAdapter(paths=env.paths, runner=env.runner, palette=NIGHTPANEL)
    assert adapter.verify("on") is False  # dark scheme, but no CSS yet

    env.gtk4.parent.mkdir(parents=True, exist_ok=True)
    env.gtk4.write_text(f"{START_SENTINEL}\nx\n")
    assert adapter.verify("on") is True
    assert adapter.verify("off") is False

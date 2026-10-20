# SPDX-License-Identifier: GPL-3.0-or-later
"""Super+P belongs to nightpanel's mini-player, and nothing may overwrite it.

Two failures lost the shortcut: nothing ever applied it at install time, and the
cheat-sheet step matched any command containing "nightpanel", so it rewrote the
player's entry. These tests pin both.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi", reason="GTK bindings required")

from nightpanel.paths import Paths  # noqa: E402
from nightpanel.services.backends.gnome import GnomeShortcutsBackend as Backend  # noqa: E402

PLAYER = {"path": "/p/custom1/", "name": "Launch Player", "command": "nightpanel --player"}
CHEAT = {
    "path": "/p/custom2/",
    "name": "Keyboard Cheat Sheet",
    "command": "nightpanel --cheat-sheet",
}


def _find(entries, app_type):
    stub = SimpleNamespace(get_custom_keybindings=lambda: entries)
    return Backend.find_custom_keybinding_by_type(stub, app_type)


def test_cheat_sheet_lookup_never_returns_the_player():
    assert _find([PLAYER, CHEAT], "cheat_sheet") is CHEAT
    assert _find([PLAYER], "cheat_sheet") is None


def test_player_lookup_finds_both_launch_forms():
    assert _find([CHEAT, PLAYER], "player") is PLAYER
    standalone = {**PLAYER, "command": "nightpanel-player"}
    assert _find([CHEAT, standalone], "player") is standalone
    assert _find([CHEAT], "player") is None


def test_music_lookup_does_not_claim_nightpanel_entries():
    assert _find([PLAYER, CHEAT], "music") is None


class _Seeder:
    """The slice of the backend ensure_player_shortcut() touches."""

    def __init__(self, tmp_path, entries=(), command="nightpanel --player", marker=False):
        self.entries = [dict(e, binding=e.get("binding", "")) for e in entries]
        self.command = command
        self.added: list[tuple[str, str, str]] = []
        self.marker = tmp_path / "player-shortcut-seeded"
        if marker:
            self.marker.touch()
        self.PLAYER_BINDING = Backend.PLAYER_BINDING

    def _player_seed_marker(self):
        return self.marker

    def get_custom_keybindings(self):
        return self.entries

    def find_custom_keybinding_by_type(self, app_type):
        return Backend.find_custom_keybinding_by_type(self, app_type)

    def detect_np_player(self):
        return self.command

    def update_custom_keybinding(self, path, command=None, **_):
        self.updated = (path, command)
        return True

    def add_custom_keybinding(self, name, command, binding):
        self.added.append((name, command, binding))
        return "/p/custom9/"


def _run(seeder):
    return Backend.ensure_player_shortcut(seeder)


def test_seeds_super_p_once(tmp_path):
    s = _Seeder(tmp_path)
    assert _run(s).startswith("added")
    assert s.added == [("Launch Player", "nightpanel --player", "<Super>p")]
    assert s.marker.exists()


def test_does_not_return_after_the_user_removes_it(tmp_path):
    s = _Seeder(tmp_path, marker=True)
    assert _run(s).startswith("skipped")
    assert s.added == []


def test_leaves_an_existing_player_entry_alone(tmp_path):
    s = _Seeder(tmp_path, entries=[{**PLAYER, "binding": "<Super>m"}])
    assert _run(s) == "present"
    assert s.added == []


def test_does_not_take_super_p_from_another_binding(tmp_path):
    other = {"path": "/p/custom3/", "name": "Display", "command": "x", "binding": "<Super>p"}
    s = _Seeder(tmp_path, entries=[other])
    assert "Super+P is already" in _run(s)
    assert s.added == [] and not s.marker.exists()


def test_skips_when_the_player_is_not_installed(tmp_path):
    s = _Seeder(tmp_path, command=None)
    assert _run(s).startswith("skipped")
    assert s.added == []


def test_marker_lives_under_the_nightpanel_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NP_CONFIG_DIR", str(tmp_path))
    assert Backend._player_seed_marker() == Paths.detect().nightpanel / "player-shortcut-seeded"
    assert Backend._player_seed_marker().parent == tmp_path


def test_repairs_a_player_entry_whose_command_became_the_cheat_sheet(tmp_path):
    """The bug that shipped: Super+P stayed bound but launched the cheat sheet."""
    broken = {
        "path": "/p/custom3/",
        "name": "NightPanel Player",
        "command": "nightpanel --cheat-sheet",
        "binding": "<Super>p",
    }
    s = _Seeder(tmp_path, entries=[broken], command="nightpanel-player")
    assert _run(s).startswith("repaired")
    assert s.updated == ("/p/custom3/", "nightpanel-player")
    assert s.added == []


def test_a_real_cheat_sheet_entry_is_not_taken_for_the_player():
    assert _find([CHEAT], "player") is None

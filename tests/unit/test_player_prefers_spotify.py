# SPDX-License-Identifier: GPL-3.0-or-later
"""The mini-player must leave a stand-in player (a browser tab) once Spotify appears."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("gi", reason="GTK bindings required")

from nightpanel import player_app  # noqa: E402

FIREFOX = "org.mpris.MediaPlayer2.firefox.instance_1_169"


def _window(bus_name):
    calls = []
    return SimpleNamespace(_bus_name=bus_name, _reconnect=lambda: calls.append(1)), calls


def _poll_choice(bus_name, on_bus):
    win, calls = _window(bus_name)
    with patch.object(player_app, "_find_mpris_players", return_value=on_bus):
        player_app.PlayerWindow._prefer_spotify(win)
    return len(calls)


def test_switches_from_firefox_when_spotify_appears():
    assert _poll_choice(FIREFOX, [FIREFOX, player_app._PREFERRED]) == 1


def test_stays_on_firefox_while_spotify_is_absent():
    assert _poll_choice(FIREFOX, [FIREFOX]) == 0


def test_does_not_reconnect_when_already_on_spotify():
    assert _poll_choice(player_app._PREFERRED, [FIREFOX, player_app._PREFERRED]) == 0

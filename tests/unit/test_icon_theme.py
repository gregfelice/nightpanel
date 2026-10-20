# SPDX-License-Identifier: GPL-3.0-or-later
"""ensure_symbolic_icons falls back to Adwaita only when symbolic icons are missing."""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gi", reason="GTK bindings required")

from nightpanel import icon_theme  # noqa: E402


def _run(has_icon: bool):
    settings = MagicMock()
    theme = MagicMock()
    theme.has_icon.return_value = has_icon
    with (
        patch.object(icon_theme.Gdk.Display, "get_default", return_value=MagicMock()),
        patch.object(icon_theme.Gtk.Settings, "get_default", return_value=settings),
        patch.object(icon_theme.Gtk.IconTheme, "get_for_display", return_value=theme),
    ):
        return icon_theme.ensure_symbolic_icons(), settings


def test_missing_icons_fall_back_to_adwaita():
    changed, settings = _run(False)
    assert changed
    assert settings.props.gtk_icon_theme_name == "Adwaita"


def test_working_theme_is_untouched():
    changed, settings = _run(True)
    assert not changed
    assert settings.props.gtk_icon_theme_name != "Adwaita"

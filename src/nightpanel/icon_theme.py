# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard against an icon theme that has no symbolic icons.

If ``org.gnome.desktop.interface icon-theme`` is set to ``hicolor`` (a fallback
theme, not a full one), every ``*-symbolic`` icon in the UI renders blank.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

logger = logging.getLogger(__name__)

PROBE_ICON = "open-menu-symbolic"
FALLBACK_THEME = "Adwaita"


def ensure_symbolic_icons() -> bool:
    """Switch this process to Adwaita when symbolic icons don't resolve.

    Returns True when the theme was overridden. Only the running process is
    affected; the user's system setting is left alone.
    """
    display = Gdk.Display.get_default()
    settings = Gtk.Settings.get_default()
    if display is None or settings is None:
        return False
    if Gtk.IconTheme.get_for_display(display).has_icon(PROBE_ICON):
        return False
    logger.warning(
        "icon theme %r has no %s; falling back to %s",
        settings.props.gtk_icon_theme_name,
        PROBE_ICON,
        FALLBACK_THEME,
    )
    settings.props.gtk_icon_theme_name = FALLBACK_THEME
    return True

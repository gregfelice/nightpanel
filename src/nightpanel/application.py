# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application class."""

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from nightpanel.icon_theme import ensure_symbolic_icons
from nightpanel.window import DailyDriverWindow

logger = logging.getLogger(__name__)


class DailyDriverApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version: str) -> None:
        super().__init__(
            application_id="io.github.gregfelice.Nightpanel",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.version = version
        self._window: DailyDriverWindow | None = None
        self._show_cheat_sheet = False

        # Add command line options
        self.add_main_option(
            "cheat-sheet",
            ord("c"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Show the keyboard shortcut cheat sheet",
            None,
        )

        # Setup actions
        self._setup_actions()

    def _setup_actions(self) -> None:
        """Set up application actions."""
        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handle command line arguments."""
        options = command_line.get_options_dict()

        # Check for --cheat-sheet flag
        self._show_cheat_sheet = options.contains("cheat-sheet")

        self.activate()
        return 0

    def do_startup(self) -> None:
        """Prepare the display before any window exists."""
        Adw.Application.do_startup(self)
        ensure_symbolic_icons()

    def do_activate(self) -> None:
        """Activate the application."""
        if not self._window:
            self._window = DailyDriverWindow(application=self)

        # Show cheat sheet if requested
        if self._show_cheat_sheet:
            self._window.show_cheat_sheet()
            self._show_cheat_sheet = False  # Reset for next acti
        self._window.present()

    def do_shutdown(self) -> None:
        """Clean up on application shutdown."""
        Adw.Application.do_shutdown(self)

    def _on_about(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        """Show about dialog."""
        about = Adw.AboutDialog(
            application_name="Daily Driver",
            application_icon="io.github.gregfelice.Nightpanel",
            developer_name="Greg Felice",
            version=self.version,
            developers=["Greg Felice"],
            copyright="Copyright 2025 Greg Felice",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/gregfelice/nightpanel",
            issue_url="https://github.com/gregfelice/nightpanel/issues",
        )
        about.present(self._window)


def main(version: str | None = None) -> int:
    """Application entry point.

    Args:
        version: Version string. If None, reads from package metadata.
            The meson launcher script passes the version from configure_file.
    """
    # --player launches nightpanel's standalone theme-synced mini-player instead
    # of the keyboard-config UI. It is a SEPARATE Adw.Application (app id
    # io.github.gregfelice.NightpanelPlayer), so we run it directly. Every
    # install path funnels through here — `nightpanel`, the Flatpak, and
    # run-dev.sh — so this single intercept makes Super+P work everywhere,
    # mirroring how --cheat-sheet is handled in the window. Pass only the program
    # name: PlayerApp doesn't parse --player and would reject the unknown option.
    # --seed-shortcuts binds Super+P to the player once and exits. The autostart entry
    # runs it at login, so the shortcut exists without opening the window.
    if "--seed-shortcuts" in sys.argv:
        from nightpanel.services.backends.gnome import GnomeShortcutsBackend

        print(GnomeShortcutsBackend().ensure_player_shortcut())
        return 0
    if "--player" in sys.argv:
        from nightpanel.player_app import PlayerApp

        return PlayerApp().run([sys.argv[0]])
    if version is None:
        try:
            from importlib.metadata import version as get_version

            version = get_version("nightpanel")
        except Exception:
            version = "0.1.0"
    app = DailyDriverApplication(version)
    return app.run(sys.argv)

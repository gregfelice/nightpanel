#!/usr/bin/env python3
"""nightpanel minimal music player — MPRIS frontend (Spotify or any MPRIS player)."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .icon_theme import ensure_symbolic_icons
from .palette import NIGHTPANEL
from .paths import Paths
from .renderers import player as _player_r

MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

_PREFERRED = "org.mpris.MediaPlayer2.spotify"

# Always active: Inter Light header title + controls in the palette accent.
_PLAYER_CSS = _player_r.render_player_css(NIGHTPANEL).encode()

# Applied when nightpanel is active: accent green for title, amber for artist.
_NP_CSS = _player_r.render_np_css(NIGHTPANEL).encode()


def _find_mpris_players() -> list[str]:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            GLib.VariantType("(as)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        names = result[0]
        return [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]
    except Exception:
        return []


def _make_proxy(bus_name: str) -> Gio.DBusProxy | None:
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            bus_name,
            MPRIS_PATH,
            PLAYER_IFACE,
            None,
        )
    except Exception:
        return None


class PlayerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("NIGHT PANEL")
        self.set_default_size(380, 100)
        self.set_resizable(False)

        self._proxy: Gio.DBusProxy | None = None
        self._bus_name: str | None = None
        self._playing = False
        self._np_active: bool | None = None

        display = Gdk.Display.get_default()

        self._player_provider = Gtk.CssProvider()
        self._player_provider.load_from_data(_PLAYER_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display,
            self._player_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._np_provider = Gtk.CssProvider()
        self._np_provider.load_from_data(_NP_CSS)

        self._build_ui()
        self._reconnect()
        self._sync_np_colors()
        GLib.timeout_add(1500, self._poll)

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(True)

        title_lbl = Gtk.Label(label="NIGHT PANEL")
        title_lbl.add_css_class("np-header-title")
        header.set_title_widget(title_lbl)

        toolbar_view.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        body.set_margin_top(8)
        body.set_margin_bottom(12)
        body.set_margin_start(14)
        body.set_margin_end(14)
        body.set_valign(Gtk.Align.CENTER)
        body.set_vexpand(True)

        # Track info
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._title_lbl = Gtk.Label(label="—")
        self._title_lbl.set_halign(Gtk.Align.START)
        self._title_lbl.set_ellipsize(3)
        self._title_lbl.add_css_class("title-4")
        self._title_lbl.add_css_class("np-title")

        self._artist_lbl = Gtk.Label(label="")
        self._artist_lbl.set_halign(Gtk.Align.START)
        self._artist_lbl.set_ellipsize(3)
        self._artist_lbl.add_css_class("caption")
        self._artist_lbl.add_css_class("np-artist")

        info.append(self._title_lbl)
        info.append(self._artist_lbl)

        # Controls
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        ctrl.set_valign(Gtk.Align.CENTER)

        self._prev_btn = self._icon_btn("media-skip-backward-symbolic", self._on_prev)
        self._play_btn = self._icon_btn("media-playback-start-symbolic", self._on_play)
        self._next_btn = self._icon_btn("media-skip-forward-symbolic", self._on_next)

        for btn in (self._prev_btn, self._play_btn, self._next_btn):
            btn.add_css_class("np-control")

        ctrl.append(self._prev_btn)
        ctrl.append(self._play_btn)
        ctrl.append(self._next_btn)

        body.append(info)
        body.append(ctrl)
        toolbar_view.set_content(body)

        self.set_content(toolbar_view)

    def _icon_btn(self, icon: str, handler) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon)
        btn.add_css_class("flat")
        btn.connect("clicked", handler)
        return btn

    # ── nightpanel color sync ─────────────────────────────────────

    def _sync_np_colors(self) -> None:
        active = (Paths.detect().nightpanel / "nightpanel-active").exists()
        if active == self._np_active:
            return
        self._np_active = active
        display = Gdk.Display.get_default()
        if active:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._np_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            self._artist_lbl.set_opacity(1.0)
        else:
            Gtk.StyleContext.remove_provider_for_display(
                display,
                self._np_provider,
            )
            self._artist_lbl.set_opacity(0.7)

    # ── Controls ──────────────────────────────────────────────────

    def _on_prev(self, _):
        self._call("Previous")

    def _on_next(self, _):
        self._call("Next")

    def _on_play(self, _):
        self._call("PlayPause")

    def _call(self, method: str) -> None:
        if not self._proxy:
            self._reconnect()
        if self._proxy:
            try:
                self._proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception:
                self._proxy = None

    # ── MPRIS polling ─────────────────────────────────────────────

    def _reconnect(self) -> bool:
        players = _find_mpris_players()
        target = _PREFERRED if _PREFERRED in players else (players[0] if players else None)
        if target:
            self._proxy = _make_proxy(target)
            self._bus_name = target if self._proxy else None
        return bool(self._proxy)

    def _poll(self) -> bool:
        self._sync_np_colors()

        if not self._proxy:
            self._reconnect()

        if self._proxy:
            try:
                self._proxy.call_sync(
                    "org.freedesktop.DBus.Properties.GetAll",
                    GLib.Variant("(s)", (PLAYER_IFACE,)),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
            except Exception:
                self._proxy = None

        if self._proxy:
            self._refresh()
        else:
            self._title_lbl.set_text("no player")
            self._artist_lbl.set_text("")

        return True

    def _refresh(self) -> None:
        try:
            meta = self._proxy.get_cached_property("Metadata")
            status = self._proxy.get_cached_property("PlaybackStatus")

            if meta:
                title = str(meta["xesam:title"]) if "xesam:title" in meta.keys() else "—"
                artists = list(meta["xesam:artist"]) if "xesam:artist" in meta.keys() else []
                artist = ", ".join(artists)
                self._title_lbl.set_text(title)
                self._artist_lbl.set_text(artist)

            playing = status and str(status) == "Playing"
            self._play_btn.set_icon_name(
                "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
            )
        except Exception:
            self._proxy = None


class PlayerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.gregfelice.NightpanelPlayer")
        self._launched_spotify = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        ensure_symbolic_icons()

    def do_activate(self):
        # If a window already exists, just raise it.
        win = self.get_active_window()
        if win:
            win.present()
            return

        if not self._spotify_on_bus():
            self._launch_spotify()

        win = PlayerWindow(application=self)
        win.connect("destroy", self._on_window_destroy)
        win.present()

    def _spotify_on_bus(self) -> bool:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result = bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                GLib.VariantType("(as)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            return _PREFERRED in result[0]
        except Exception:
            return False

    def _launch_spotify(self) -> None:
        try:
            Gio.Subprocess.new(["spotify"], Gio.SubprocessFlags.NONE)
            self._launched_spotify = True
        except Exception:
            pass

    def _on_window_destroy(self, _win) -> None:
        if self._launched_spotify:
            self._quit_spotify()
        self.quit()

    def _quit_spotify(self) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                _PREFERRED,
                "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2",
                "Quit",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
        except Exception:
            pass


def main():
    app = PlayerApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()

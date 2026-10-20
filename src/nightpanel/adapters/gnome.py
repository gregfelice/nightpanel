# SPDX-License-Identifier: GPL-3.0-or-later
"""GNOME adapter — gsettings color-scheme + background + GTK CSS overlay.

GTK4 reads ``~/.config/gtk-4.0/gtk.css`` exactly once per display in
``settings_init_style()`` (gtk/gtksettings.c). The static ``GtkCssProvider``
it allocates is never reloaded — there is no GFileMonitor on the file and
no documented out-of-process hook (DBus, signal, XSettings) to ask a
running GTK4 app to re-parse it.

libadwaita's color-scheme change channel (XDG portal
``org.freedesktop.portal.Settings`` → ``org.freedesktop.appearance``
``color-scheme``) re-renders the *already-parsed* CSS through media
queries, so gsettings dark-mode toggles propagate live — but the on-disk
gtk.css is not re-read. That's why a long-running Nautilus shows
"standard Adwaita dark" after toggling NP on but doesn't pick up the NP
overrides written between its startup and the toggle.

The only reliable recipe is to bounce the running GTK4 app. We do this
for Nautilus specifically (``gapplication action org.gnome.Nautilus
kill`` → ``g_application_quit()``) on both apply and revert. Other
GTK4 apps started before nightpanel applies will keep stale CSS until
the user restarts them — documented caveat.

Refs:
  https://gitlab.gnome.org/GNOME/gtk/-/raw/main/gtk/gtksettings.c
  https://gitlab.gnome.org/GNOME/libadwaita/-/raw/main/src/adw-style-manager.c
  https://gitlab.gnome.org/GNOME/nautilus/-/raw/main/src/nautilus-application.c
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from ..renderers import gtk as _renderer
from ..renderers.gtk import END_SENTINEL, START_SENTINEL
from .base import Adapter, AdapterError

_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
_BACKGROUND_SCHEMA = "org.gnome.desktop.background"


class GnomeAdapter(Adapter):
    name = "gnome"

    @property
    def _gtk4_css(self) -> Path:
        return self.paths.config / "gtk-4.0" / "gtk.css"

    @property
    def _gtk3_css(self) -> Path:
        return self.paths.config / "gtk-3.0" / "gtk.css"

    def _keep_wallpaper(self) -> bool:
        """Opt-out flag: if ``<config-dir>/keep-wallpaper`` exists, apply() leaves
        the desktop background alone instead of blacking it out. Default (no file)
        is to black out, per the design."""
        return (self.paths.nightpanel / "keep-wallpaper").exists()

    def installed(self) -> bool:
        return bool(shutil.which("gsettings"))

    def restorable_paths(self) -> list[Path]:
        return [self._gtk4_css, self._gtk3_css]

    def snapshot(self) -> dict:
        return {
            "color_scheme": self._gsettings_get(_INTERFACE_SCHEMA, "color-scheme"),
            "bg_uri": self._gsettings_get(_BACKGROUND_SCHEMA, "picture-uri"),
            "bg_uri_dark": self._gsettings_get(_BACKGROUND_SCHEMA, "picture-uri-dark"),
            "bg_color": self._gsettings_get(_BACKGROUND_SCHEMA, "primary-color"),
            "bg_color2": self._gsettings_get(_BACKGROUND_SCHEMA, "secondary-color"),
            "bg_shading": self._gsettings_get(_BACKGROUND_SCHEMA, "color-shading-type"),
            "bg_options": self._gsettings_get(_BACKGROUND_SCHEMA, "picture-options"),
            "files": {str(p): self.capture(p) for p in self.restorable_paths()},
        }

    def apply(self, palette: Palette) -> None:
        self._gsettings_set(_INTERFACE_SCHEMA, "color-scheme", "prefer-dark")
        # Black out the desktop to the palette canvas by DEFAULT — the design is a
        # pure black background (Saab night-panel aesthetic). Opt out by creating
        # <config-dir>/keep-wallpaper. Clear BOTH picture-uri and picture-uri-dark
        # (dark mode reads the -dark key, so clearing only picture-uri leaves the
        # wallpaper up) and set picture-options=none so the solid primary-color
        # shows. Also force color-shading-type=solid: if the user's pre-existing
        # shading is horizontal/vertical, GNOME paints a gradient from
        # primary-color to secondary-color (observed as a black→blue gradient),
        # NOT a solid fill — so pin both shading and secondary to the canvas.
        # snapshot()/revert() restore the user's wallpaper + shading on
        # toggle-off regardless.
        if not self._keep_wallpaper():
            self._gsettings_set(_BACKGROUND_SCHEMA, "picture-uri", "")
            self._gsettings_set(_BACKGROUND_SCHEMA, "picture-uri-dark", "")
            self._gsettings_set(_BACKGROUND_SCHEMA, "primary-color", palette.bg)
            self._gsettings_set(_BACKGROUND_SCHEMA, "secondary-color", palette.bg)
            self._gsettings_set(_BACKGROUND_SCHEMA, "color-shading-type", "solid")
            self._gsettings_set(_BACKGROUND_SCHEMA, "picture-options", "none")
        self._apply_gtk_css(palette)
        self._bounce_nautilus()

    def revert(self, snapshot: dict) -> None:
        files = snapshot.get("files") or {}
        if not files:
            raise AdapterError(f"{self.name}: no capture for the gtk css files; refusing to revert")
        # Restore CSS before color-scheme so GTK reloads with the right file in one pass.
        for path in self.restorable_paths():
            self.restore(path, files.get(str(path)) or {})
        self._bounce_nautilus()

        cs = snapshot.get("color_scheme") or "default"
        self._gsettings_set(_INTERFACE_SCHEMA, "color-scheme", cs)
        for key, gkey in (
            ("bg_uri", "picture-uri"),
            ("bg_uri_dark", "picture-uri-dark"),
            ("bg_color", "primary-color"),
            ("bg_color2", "secondary-color"),
            ("bg_shading", "color-shading-type"),
        ):
            if snapshot.get(key):
                self._gsettings_set(_BACKGROUND_SCHEMA, gkey, snapshot[key])
        self._gsettings_set(
            _BACKGROUND_SCHEMA, "picture-options", snapshot.get("bg_options") or "zoom"
        )

    def verify(self, expected: Literal["on", "off"]) -> bool:
        cs = self._gsettings_get(_INTERFACE_SCHEMA, "color-scheme") or ""
        gtk = self.read_text(self._gtk4_css) if self._gtk4_css.exists() else ""
        on = cs == "prefer-dark" and START_SENTINEL in gtk
        return on if expected == "on" else not on

    # ── helpers ──
    def _gsettings_get(self, schema: str, key: str) -> str | None:
        result = self.run(["gsettings", "get", schema, key])
        return result.strip("'") if result else None

    def _gsettings_set(self, schema: str, key: str, value: str) -> None:
        self.run_checked(["gsettings", "set", schema, key, value])

    def _bounce_nautilus(self) -> None:
        """Force a running Nautilus to re-read gtk.css by quitting it.

        GTK4 only re-parses ``~/.config/gtk-4.0/gtk.css`` at app startup; see
        module docstring. ``gapplication action org.gnome.Nautilus kill``
        routes to ``g_application_quit()`` and is the supported clean exit
        (same as ``nautilus --quit``). Non-zero exit is normal when Nautilus
        isn't registered on the bus — no instance to bounce, nothing to do.
        """
        self.run_optional(
            ["gapplication", "action", "org.gnome.Nautilus", "kill"],
            "no Nautilus instance is registered on the bus; nothing to bounce",
        )

    def _apply_gtk_css(self, palette: Palette) -> None:
        """Render the NP CSS from the palette and prepend it to gtk.css.

        Strips any prior NP-sentinel-wrapped block first so palette edits
        propagate on re-apply (without the strip, the file would only ever
        contain the very first apply's CSS).
        """
        css = _renderer.render(palette)
        for path in self.restorable_paths():
            existing = self.read_text(path) if path.exists() else ""
            self.write(path, css + self._strip_np_block(existing))

    @staticmethod
    def _strip_np_block(text: str) -> str:
        """Remove every NP block between paired sentinels (inclusive).

        Robust to multiple blocks (defensive — shouldn't happen, but if it
        ever did during an interrupted apply, we want re-apply to be idempotent).
        Idempotent: returns ``text`` unchanged when no sentinels are present.
        """
        out = text
        while True:
            start = out.find(START_SENTINEL)
            end = out.find(END_SENTINEL, start)
            if start < 0 or end < 0:
                break
            cut_end = end + len(END_SENTINEL)
            if cut_end < len(out) and out[cut_end] == "\n":
                cut_end += 1
            out = out[:start] + out[cut_end:]
        return out

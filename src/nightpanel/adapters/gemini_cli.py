# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini CLI adapter — custom-theme write + truecolor-stripping wrapper.

Mirrors the claude_code adapter's two-pronged strategy: a JSON setting
swap plus a wrapper that strips COLORTERM behind a sentinel file. The
combination ensures every gemini emission flows through alacritty's
repainted ANSI palette (strict green/amber/red) instead of the raw
truecolor hex that gemini would otherwise emit.

The custom theme mixes two color forms intentionally:

* **ANSI named** ("green", "yellow", "blue", "greenbright" …) for every
  text and status slot. Under the wrapper's COLORTERM strip these emit
  16-color escapes, which alacritty paints to its saab palette exactly.
* **Hex** for the backgrounds. Gemini derives InputBackground /
  MessageBackground / FocusBackground via ``interpolateColor(bg, gray,
  opacity)``; that math wants real numerics on both ends. Passing named
  ANSI colors confuses tinygradient (it reads "blue" as CSS blue rather
  than alacritty's repainted ghost-green), producing a bright-blue
  input box. Hex on both ends keeps the interpolated derivatives near
  the terminal canvas.

Revert puts both files back exactly as capture found them — the settings
file byte-for-byte, and the wrapper as the symlink the installer created.
The previous version captured the wrapper's existence and never restored it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from .base import Adapter, AdapterError

_NP_THEME = "nightpanel"  # the customTheme name we write + engage
_DEFAULT_THEME = "Default"  # what the CLI falls back to when we remove our block

_WRAPPER_MARKER = "# nightpanel-gemini-wrapper"


class GeminiCliAdapter(Adapter):
    name = "gemini_cli"

    @property
    def _settings(self) -> Path:
        return self.paths.home / ".gemini" / "settings.json"

    @property
    def _wrapper(self) -> Path:
        return self.paths.bin / "gemini"

    def installed(self) -> bool:
        return bool(shutil.which("gemini")) or self._settings.exists()

    def restorable_paths(self) -> list[Path]:
        return [self._settings, self._wrapper]

    def snapshot(self) -> dict:
        captured = self.capture(self._settings)
        theme = self._parse_theme(captured["content"]) if captured["existed"] else None
        # Don't record our own applied value as the baseline — revert would then
        # leave the user staring at "nightpanel" after the customTheme block is
        # gone. Fall through to the CLI default. Only when the file existed and its
        # theme was ours: a file that did not exist must be removed by revert, and a
        # file that had no theme must come back exactly as it was.
        ours = bool(captured["existed"] and theme == _NP_THEME)
        return {
            "settings": captured,
            "theme": None if ours else theme,
            "baseline_was_ours": ours,
            "wrapper": self.capture(self._wrapper),
        }

    def apply(self, palette: Palette) -> None:
        self._write_custom_theme(palette)
        self._write_theme(_NP_THEME)
        self._install_wrapper()

    def revert(self, snapshot: dict) -> None:
        captured = snapshot.get("settings")
        if not captured:
            raise AdapterError(
                f"{self.name}: no capture for {self._settings}; refusing to revert on a guess"
            )
        if snapshot.get("baseline_was_ours"):
            # The baseline we recorded was our own value, so byte-for-byte
            # restore would re-engage the theme whose customTheme block revert
            # has just removed. This is the one deliberate exception to the
            # round-trip, and it only applies to a file that existed before us —
            # a file we created is removed, not rewritten with a default.
            data = self._parse_json(captured["content"])
            ui = data.get("ui", {})
            ui.pop("customThemes", None)
            ui["theme"] = _DEFAULT_THEME
            self.write(self._settings, json.dumps(data, indent=2) + "\n")
        else:
            self.restore(self._settings, captured)

        wrapper = snapshot.get("wrapper")
        if not wrapper:
            raise AdapterError(
                f"{self.name}: no capture for {self._wrapper}; refusing to revert on a guess"
            )
        self.restore(self._wrapper, wrapper)

    def verify(self, expected: Literal["on", "off"]) -> bool:
        theme_on = self._read_theme() == _NP_THEME
        wrapper_on = self._wrapper_installed()
        on = theme_on and wrapper_on
        return on if expected == "on" else not on

    # ── helpers ──
    def _parse_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise AdapterError(f"{self.name}: {self._settings} is not valid JSON: {e}") from e

    def _parse_theme(self, text: str) -> str | None:
        return self._parse_json(text).get("ui", {}).get("theme")

    def _read_settings(self) -> dict:
        if not self._settings.exists():
            # A fresh profile has no prior state. That is not a read failure, and
            # apply() is expected to create the file.
            return {}
        return self._parse_json(self.read_text(self._settings))

    def _read_theme(self) -> str | None:
        try:
            return self._read_settings().get("ui", {}).get("theme")
        except AdapterError:
            return None

    def _write_theme(self, value: str) -> None:
        data = self._read_settings()
        if "ui" not in data:
            data["ui"] = {}
        data["ui"]["theme"] = value
        self.write(self._settings, json.dumps(data, indent=2) + "\n")

    def _write_custom_theme(self, p: Palette) -> None:
        """Write customThemes.nightpanel with the saab green/amber mapping.

        See module docstring for why text/status use ANSI named colors
        while backgrounds use hex.
        """
        data = self._read_settings()
        if "ui" not in data:
            data["ui"] = {}
        if "customThemes" not in data["ui"]:
            data["ui"]["customThemes"] = {}
        data["ui"]["customThemes"][_NP_THEME] = {
            "name": _NP_THEME,
            "type": "custom",
            # All backgrounds clamped to the alacritty canvas. Setting
            # text.secondary as a hex (below) keeps the interpolated
            # InputBackground / MessageBackground / FocusBackground very close
            # to canvas too. The schema is strict — additionalProperties:false
            # on `background`, so only `primary` and `diff` are accepted. Adding
            # `prompt` (which earlier hand-written variants of this theme had)
            # makes gemini reject the entire customTheme and silently fall back
            # to Default — a single shade of green. Don't add it.
            "background": {
                "primary": p.bg,
                "diff": {"added": p.bg, "removed": p.bg},
            },
            "text": {
                # ANSI named — alacritty paints normal.green = p.fg (sage).
                "primary": "green",
                # Hex — drives gemini's bg interpolation. fg_mid avoids
                # landing on any of the alacritty 256-color remap slots that
                # would render the text as canvas-invisible.
                "secondary": p.fg_mid,
                # bright.green = p.fg_bright (live green).
                "link": "greenbright",
                # normal.yellow = p.fg_amber (saab needle).
                "accent": "yellow",
                "response": "green",
            },
            "status": {
                "success": "greenbright",
                "warning": "yellow",
                "error": "red",
            },
            "ui": {
                # normal.blue = p.fg_dim (ghost-green) — comments dim.
                "comment": "blue",
                # Brackets / separators picked out in amber.
                "symbol": "yellow",
                "gradient": ["blue", "green", "greenbright"],
            },
            # Borders come from DarkGray. Setting it explicitly skips gemini's
            # default interpolation (which would yield a faint green tint) and
            # gives a flat subtle grey instead.
            "DarkGray": p.border_d,
        }
        self.write(self._settings, json.dumps(data, indent=2) + "\n")

    def _wrapper_installed(self) -> bool:
        """True iff the on-disk wrapper exactly matches this instance's script.

        Marker-only comparison is unsafe: an earlier version of this adapter
        wrote a dumber passthrough script that carried the same marker, so a
        marker check would (and did) leave that stale script in place after the
        COLORTERM-stripping rewrite. Comparing full content makes every wrapper
        change self-healing on the next apply().
        """
        if not self._wrapper.exists() or self._wrapper.is_symlink():
            return False
        try:
            return self.read_text(self._wrapper) == self._wrapper_script()
        except OSError:
            return False

    def _wrapper_script(self) -> str:
        """The wrapper, generated from this instance's paths.

        A module-level f-string baked the developer's home directory into every
        generated script, so a wrapper written on one machine pointed at another
        machine's tree.
        """
        marker = _WRAPPER_MARKER
        active = self.paths.nightpanel / "nightpanel-active"
        return f"""\
#!/usr/bin/env bash
{marker}
# nightpanel-aware Gemini CLI wrapper.
# When nightpanel is active, strips COLORTERM so any truecolor output
# falls back to 16-color rendering — every escape flows through the
# terminal ANSI palette (which nightpanel paints in strict
# green/amber/red). When nightpanel is off, the wrapper is a transparent
# passthrough. Generated by nightpanel.adapters.gemini_cli — regenerate
# by calling apply().

# Resolve the real binary through PATH rather than a baked-in absolute path, and
# skip this wrapper itself — ~/.local/bin is normally ahead of the system dirs,
# so a plain `command -v gemini` finds the wrapper and recurses forever.
REAL=""
for d in ${{PATH//:/ }}; do
    if [ -x "$d/gemini" ] && [ "$d/gemini" != "$0" ]; then
        REAL="$d/gemini"
        break
    fi
done
if [ -z "$REAL" ]; then
    echo "gemini: real binary not found on PATH" >&2
    exit 127
fi

if [ -e "{active}" ]; then
    exec env -u COLORTERM "$REAL" "$@"
else
    exec "$REAL" "$@"
fi
"""

    def _install_wrapper(self) -> None:
        if self._wrapper_installed():
            return
        self.write(self._wrapper, self._wrapper_script())
        try:
            self._wrapper.chmod(0o755)
        except OSError as e:
            raise AdapterError(
                f"{self.name}: could not make {self._wrapper} executable: {e}"
            ) from e

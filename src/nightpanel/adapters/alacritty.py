# SPDX-License-Identifier: GPL-3.0-or-later
"""Alacritty adapter — flips the ``[general].import`` list in alacritty.toml.

Edits are done structurally with tomlkit (format/comment preserving) rather
than by regex. The regex approach only understood single-line
``import = ["…"]`` and silently corrupted the multi-line array form
(``import = [\\n  "…"\\n]``): the strip matched just the ``import = [`` line and
orphaned the array body, leaving invalid TOML that alacritty refused to load.

Revert restores the captured file byte-for-byte. It refuses when there is no
capture: an empty snapshot cannot tell "the user had no import list" from "we
never looked", and the first reading would delete a user's import line.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

import tomlkit
from tomlkit.exceptions import TOMLKitError

from ..palette import Palette
from ..renderers import alacritty as _renderer
from .base import Adapter, AdapterError

_NP_THEME_NAME = "nightpanel.toml"


class AlacrittyAdapter(Adapter):
    name = "alacritty"

    @property
    def _cfg(self) -> Path:
        return self.paths.config / "alacritty" / "alacritty.toml"

    @property
    def _np_theme(self) -> Path:
        return self.paths.config / "alacritty" / "themes" / "themes" / _NP_THEME_NAME

    def installed(self) -> bool:
        return bool(shutil.which("alacritty")) or self._cfg.exists()

    def restorable_paths(self) -> list[Path]:
        # Only the user's file.
        return [self._cfg]

    def own_paths(self) -> list[Path]:
        # The rendered theme is ours, regenerated on every apply — but only when we
        # created it. A theme file already standing there is user state, so snapshot()
        # captures it and revert puts it back.
        return [self._np_theme]

    def snapshot(self) -> dict:
        return {
            "file": self.capture(self._cfg),
            "import": self._read_import(),
            "own": self.capture_own_paths(),
        }

    def apply(self, palette: Palette) -> None:
        # Regenerate the theme file from the palette on every apply so palette
        # changes propagate. Then point alacritty.toml's import at only it.
        self.write(self._np_theme, _renderer.render(palette))
        self._set_import([str(self._np_theme)])

    def revert(self, snapshot: dict) -> None:
        captured = snapshot.get("file")
        if not captured:
            raise AdapterError(f"{self.name}: no capture for {self._cfg}; refusing to revert")
        self.restore(self._cfg, captured)
        self.restore_own_paths(snapshot)

    def verify(self, expected: Literal["on", "off"]) -> bool:
        imp = self._read_import() or []
        on = any("nightpanel" in p for p in imp)
        return on if expected == "on" else not on

    # ── helpers ──
    def _read_import(self) -> list[str] | None:
        doc = self._load()
        if doc is None:
            return None
        imp = doc.get("general", {}).get("import")
        if imp is None:
            return None
        return [str(x) for x in imp]

    def _load(self) -> tomlkit.TOMLDocument | None:
        if not self._cfg.exists():
            return None
        try:
            return tomlkit.parse(self.read_text(self._cfg))
        except (OSError, TOMLKitError) as e:
            # A corrupt alacritty.toml is a real problem the user should see,
            # not a silent no-op: the previous behaviour made apply() write a
            # brand-new file over an unreadable one, destroying the user's config.
            raise AdapterError(f"{self.name}: alacritty.toml unreadable: {e}") from e

    def _set_import(self, value: list[str] | None) -> None:
        """Set ``[general].import`` to ``value``, or remove it when ``None``."""
        doc = self._load()
        if doc is None:
            if value is None:
                return
            doc = tomlkit.document()
        general = doc.get("general")
        if general is None:
            if value is None:
                return
            general = tomlkit.table()
            doc["general"] = general
        if value is None:
            general.pop("import", None)
        else:
            general["import"] = value
        self.write(self._cfg, tomlkit.dumps(doc))

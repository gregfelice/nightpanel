# SPDX-License-Identifier: GPL-3.0-or-later
"""nvim adapter — sets g:colors_name via an after/plugin override.

Lives in ``<config>/nvim/after/plugin/nightpanel_active.lua``. Created on
apply, removed on revert — or restored byte-for-byte if the user already had a
file there, which the previous version deleted unconditionally.

Live nvim instances (those exposing --server sockets) are notified directly so
they switch colorschemes immediately.
"""

from __future__ import annotations

import glob
import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from .base import Adapter, AdapterError

_DEFAULT_CS = "tokyonight"
_NP_CS = "nightpanel"


class NvimAdapter(Adapter):
    name = "nvim"

    @property
    def _after_plugin(self) -> Path:
        return self.paths.config / "nvim" / "after" / "plugin" / "nightpanel_active.lua"

    def installed(self) -> bool:
        return bool(shutil.which("nvim"))

    def restorable_paths(self) -> list[Path]:
        return [self._after_plugin]

    def snapshot(self) -> dict:
        return {
            "file": self.capture(self._after_plugin),
            "colorscheme": self._query_live_colorscheme() or _DEFAULT_CS,
        }

    def apply(self, palette: Palette) -> None:
        self.write(self._after_plugin, f"vim.cmd('colorscheme {_NP_CS}')\n")
        self._send_cmd(f"colorscheme {_NP_CS}")

    def revert(self, snapshot: dict) -> None:
        captured = snapshot.get("file")
        if not captured:
            raise AdapterError(
                f"{self.name}: no capture for {self._after_plugin}; refusing to revert"
            )
        self.restore(self._after_plugin, captured)
        prev = snapshot.get("colorscheme") or _DEFAULT_CS
        self._send_cmd(f"colorscheme {prev}")

    def verify(self, expected: Literal["on", "off"]) -> bool:
        # Keyed on content, not existence: if the user had their own file here
        # before nightpanel, existence alone would report "on" after revert.
        on = self._after_plugin.exists() and self.read_text(self._after_plugin).strip() == (
            f"vim.cmd('colorscheme {_NP_CS}')"
        )
        return on if expected == "on" else not on

    # ── helpers ──
    def _sockets(self) -> list[str]:
        # Modern nvim puts sockets in $XDG_RUNTIME_DIR/nvim.<pid>.0. The old
        # pattern list hardcoded /tmp and missed it.
        patterns = [
            str(self.paths.runtime / "nvim.*"),
            str(self.paths.state / "nvim" / "*.sock"),
        ]
        out: list[str] = []
        for p in patterns:
            out.extend(glob.glob(p))
        return out

    def _query_live_colorscheme(self) -> str | None:
        for sock in self._sockets():
            result = self.run(["nvim", "--server", sock, "--remote-expr", "g:colors_name"])
            if result:
                return result
        return None

    def _send_cmd(self, cmd: str) -> None:
        for sock in self._sockets():
            self.run_optional(
                ["nvim", "--server", sock, "--remote-send", f"<Esc>:silent! {cmd}<CR>"],
                "no nvim server socket is running; the override file is correct at rest",
            )

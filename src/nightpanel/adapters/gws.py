# SPDX-License-Identifier: GPL-3.0-or-later
"""gws (Getting Work Sorted) adapter — flips the theme in gws's state file.

gws is a Rust TUI GTD app. Its theme selection is persisted as a ``theme:<Name>``
line in the sibling ``.state`` file (default ``~/.gws/todo.state``). gws ships
with a ``Nightpanel`` theme that mirrors this project's palette — the adapter
writes ``theme:Nightpanel`` on apply, restores the previous theme on revert.

This is the model the other adapters should follow: it records *existence*
explicitly, so revert can tell "the file did not exist before us" from "the
file existed with a different theme", and it preserves the other lines.

Caveats:
- gws reads its state file only on startup and writes it on exit. If gws is
  RUNNING when the adapter toggles, our write is silently clobbered by gws's
  exit-time save. Workaround: close gws before toggling, or relaunch after.
- The adapter preserves the other lines (cat:/proj:/task: collapse markers)
  in the state file when rewriting.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from .base import Adapter, AdapterError

_NP_THEME = "Nightpanel"


def _parse(state_text: str) -> tuple[str | None, list[str]]:
    """Split state file content into (theme_value_or_None, other_lines)."""
    theme: str | None = None
    others: list[str] = []
    for line in state_text.splitlines():
        if line.startswith("theme:"):
            theme = line[len("theme:") :]
        else:
            others.append(line)
    return theme, others


def _serialize(theme: str | None, others: list[str]) -> str:
    lines: list[str] = []
    if theme:
        lines.append(f"theme:{theme}")
    lines.extend(others)
    content = "\n".join(lines)
    return content + "\n" if content else ""


class GwsAdapter(Adapter):
    name = "gws"

    @property
    def _state(self) -> Path:
        return self.paths.home / ".gws" / "todo.state"

    def installed(self) -> bool:
        # Binary on PATH or the state dir exists (gws has been run at least once).
        return bool(shutil.which("gws")) or self._state.parent.exists()

    def restorable_paths(self) -> list[Path]:
        return [self._state]

    def snapshot(self) -> dict:
        captured = self.capture(self._state)
        theme, _ = _parse(captured["content"] or "") if captured["existed"] else (None, [])
        return {"file": captured, "theme": theme}

    def apply(self, palette: Palette) -> None:
        self._set_theme(_NP_THEME)

    def revert(self, snapshot: dict) -> None:
        captured = snapshot.get("file")
        if not captured:
            raise AdapterError(f"{self.name}: no capture for {self._state}; refusing to revert")
        if not captured["existed"]:
            # State file didn't exist before us. If our theme line is now the
            # only content, drop the whole file; otherwise just strip our line
            # and leave the rest.
            self._set_theme(None, drop_if_empty=True)
            return
        self.restore(self._state, captured)

    def verify(self, expected: Literal["on", "off"]) -> bool:
        if not self._state.exists():
            return expected == "off"
        theme, _ = _parse(self.read_text(self._state))
        on = theme == _NP_THEME
        return on if expected == "on" else not on

    # ── helpers ──
    def _set_theme(self, theme: str | None, *, drop_if_empty: bool = False) -> None:
        if not self._state.exists():
            if drop_if_empty and not theme:
                return
            self.write(self._state, _serialize(theme, []))
            return
        _, others = _parse(self.read_text(self._state))
        content = _serialize(theme, others)
        if drop_if_empty and not content.strip():
            try:
                self._state.unlink(missing_ok=True)
            except OSError as e:
                raise AdapterError(f"{self.name}: could not remove {self._state}: {e}") from e
            return
        self.write(self._state, content)

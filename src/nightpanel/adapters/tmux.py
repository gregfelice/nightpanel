# SPDX-License-Identifier: GPL-3.0-or-later
"""tmux adapter — sources a palette overlay over the user's base theme.

Snapshot captures the live value of every option the overlay sets, so revert
restores what it actually captured. The previous version returned ``{}`` and
reverted by re-sourcing ``~/.tmux.conf`` — which is not restoration, it is a
guess that happens to be right when the user's base conf is the source of the
theme. If the user had set any of these options by hand, or had sourced a
different theme after nightpanel, re-sourcing silently overwrote it.

Capturing the options is what makes the round trip honest.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from ..renderers import tmux as _renderer
from .base import Adapter, AdapterError

#: every option the overlay sets. snapshot() captures these, revert() restores
#: them. Adding an option to the renderer means adding it here.
_OPTIONS = [
    "status-style",
    "status-left",
    "status-left-length",
    "status-right-length",
    "status-right",
    "pane-border-style",
    "pane-active-border-style",
    "message-style",
    "message-command-style",
    "mode-style",
    "window-status-format",
    "window-status-current-format",
]


class TmuxAdapter(Adapter):
    name = "tmux"

    @property
    def _overlay(self) -> Path:
        return self.paths.nightpanel / "themes" / "tmux-nightpanel.conf"

    def installed(self) -> bool:
        return bool(shutil.which("tmux"))

    def own_paths(self) -> list[Path]:
        # The overlay is ours: rendered from the palette on every apply, so there is
        # no user state in it to put back. The user's live options are the state, and
        # snapshot() captures those.
        return [self._overlay]

    def snapshot(self) -> dict:
        """Capture the live value of every option the overlay will overwrite.

        A ``None`` value means "no live tmux server, nothing to capture" — that
        is an honest answer. An empty dict is not, which is what this adapter
        used to return.
        """
        return {opt: self.run(["tmux", "show", "-gv", opt]) for opt in _OPTIONS}

    def apply(self, palette: Palette) -> None:
        self.write(self._overlay, _renderer.render(palette))
        # No server running is a normal state: the overlay is correct at rest
        # and tmux picks it up on the next start. That is an outcome, not success.
        self.run_optional(
            ["tmux", "source-file", str(self._overlay)],
            "no tmux server running; the overlay is correct at rest",
        )

    def revert(self, snapshot: dict) -> None:
        if not snapshot:
            # Re-sourcing ~/.tmux.conf is not restoration; it is a guess, and it
            # overwrites options the user set by hand. Refuse instead.
            raise AdapterError(f"{self.name}: no capture; refusing to revert on a guess")
        for opt, value in snapshot.items():
            if value is None:
                continue  # nothing was live to capture, nothing to put back
            self.run_optional(
                ["tmux", "set", "-g", opt, value],
                "no tmux server running; the captured value cannot be applied",
            )

    def verify(self, expected: Literal["on", "off"]) -> bool:
        live = self.run(["tmux", "show", "-gv", "status-style"]) or ""
        # Compare against what the renderer would produce for this palette,
        # instead of testing for a hardcoded "#000000" that only held for one
        # palette and broke the moment the palette changed.
        on = f"bg={self.palette.bg_header}" in live.lower()
        return on if expected == "on" else not on

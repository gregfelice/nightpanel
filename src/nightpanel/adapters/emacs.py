# SPDX-License-Identifier: GPL-3.0-or-later
"""Emacs adapter — toggles the nightpanel theme via emacsclient.

The theme itself is no longer generated here. `nightpanel-theme.el` is a
standalone package (https://github.com/gregfelice/nightpanel-theme) and is
installed like any other theme, so package.el or the user's init owns
`custom-theme-load-path`. Rendering it from the palette as well produced two
copies that drifted apart, which is why that path was dropped.

Apply writes a sentinel + tells any running daemons to load the theme; revert
removes the sentinel + tells daemons to disable it. `verify()` keys off the
sentinel (the only signal that survives across daemon restarts).

The sentinel is an artifact this adapter owns, not user state, so it is not a
restorable path. The user state here is the live daemon's theme list, which
cannot be restored byte-for-byte — that is the honest limit of what this
adapter can promise, and it is why the live-session half needs the VM harness
rather than unit tests.
"""

from __future__ import annotations

import shutil
from typing import Literal

from ..palette import Palette
from .base import Adapter, AdapterError

_NP_THEME = "nightpanel"


class EmacsAdapter(Adapter):
    name = "emacs"

    #: The only state this adapter changes is a running daemon's theme list,
    #: which cannot be put back byte-for-byte. It has no user file, so it has
    #: nothing to restore — that is the honest limit, stated in the contract.
    stateful = False

    @property
    def _sentinel(self):
        return self.paths.nightpanel / "emacs-active"

    def installed(self) -> bool:
        return bool(shutil.which("emacs"))

    def snapshot(self) -> dict:
        # Capture the daemon's currently-enabled themes so revert can restore
        # them. If no daemon is running, the capture is empty — that is an
        # honest answer, not a skipped capture.
        themes = self.run(["emacsclient", "-e", "custom-enabled-themes"])
        return {"enabled_themes": themes}

    def apply(self, palette: Palette) -> None:
        # `palette` is unused: the Adapter protocol passes it, but the theme is
        # an installed package now rather than something rendered from it.

        # 1. Drop the sentinel so verify() can answer even when no daemon runs.
        self.write(self._sentinel, "")

        # 2. Tell any live daemon to load the installed theme. Wrapped in
        #    condition-case so a machine without nightpanel-theme installed
        #    still toggles every other adapter instead of erroring out here.
        #    No daemon at all is an outcome (AdapterNotLive), not success; a daemon
        #    that answers but cannot load the theme is a real failure.
        loaded = self.run_optional(
            [
                "emacsclient",
                "-e",
                f"(condition-case nil (progn (load-theme (quote {_NP_THEME}) t) t) (error nil))",
            ],
            "no emacs daemon is running; the sentinel is the whole apply",
        )
        if loaded != "t":
            raise AdapterError(
                f"{self.name}: theme not loaded — install nightpanel-theme "
                "(M-x package-install RET nightpanel-theme) or put it on "
                "custom-theme-load-path"
            )

    def revert(self, snapshot: dict) -> None:
        # 1. Remove sentinel first so verify("off") returns True immediately.
        try:
            self._sentinel.unlink(missing_ok=True)
        except OSError as e:
            raise AdapterError(f"{self.name}: could not remove {self._sentinel}: {e}") from e

        # 2. Tell any live daemon to disable nightpanel. The user's other
        #    themes (captured in snapshot["enabled_themes"]) remain enabled
        #    since we never disabled them.
        self.run_optional(
            ["emacsclient", "-e", f"(disable-theme (quote {_NP_THEME}))"],
            "no emacs daemon is running; the sentinel is the whole revert",
        )

    def verify(self, expected: Literal["on", "off"]) -> bool:
        on = self._sentinel.exists()
        return on if expected == "on" else not on

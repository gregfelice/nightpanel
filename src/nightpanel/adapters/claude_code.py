# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude Code adapter — theme setting swap + truecolor-stripping wrapper.

Two-pronged strategy, mirrored by the gemini_cli adapter:

* a JSON setting swap (``theme``) for Claude Code's own UI,
* a wrapper that strips ``COLORTERM`` behind the nightpanel-active sentinel,
  so Claude's output falls back to 16-color rendering and every escape flows
  through the terminal ANSI palette (which nightpanel paints in strict
  green/amber/red) instead of raw truecolor hex.

The wrapper replaces whatever ``~/.local/bin/claude`` was — normally the
installer's symlink into ``versions/``. That is user state, so it is captured
and put back. The previous version of this file captured the link in
``snapshot()`` and never used it in ``revert()``, which meant toggling nightpanel
off left the wrapper behind forever. Restoring the link is behaviourally
equivalent (the wrapper is a transparent passthrough when nightpanel is off) and
strictly better: an off state leaves nothing behind, and the round-trip becomes
provable by the contract battery.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from ..palette import Palette
from .base import Adapter, AdapterError

_NP_THEME = "dark"  # the preset we engage when nightpanel is on

_WRAPPER_MARKER = "# nightpanel-claude-wrapper"


class ClaudeCodeAdapter(Adapter):
    name = "claude_code"

    @property
    def _settings(self) -> Path:
        return self.paths.home / ".claude" / "settings.json"

    @property
    def _wrapper(self) -> Path:
        return self.paths.bin / "claude"

    @property
    def _real_dir(self) -> Path:
        return self.paths.home / ".local" / "share" / "claude" / "versions"

    def installed(self) -> bool:
        # The CLI may not be on $PATH if installed via a manager; check the
        # settings dir instead. Either signal is good enough.
        return bool(shutil.which("claude")) or self._settings.exists()

    def restorable_paths(self) -> list[Path]:
        return [self._settings, self._wrapper]

    def snapshot(self) -> dict:
        return {
            "settings": self.capture(self._settings),
            "wrapper": self.capture(self._wrapper),
        }

    def apply(self, palette: Palette) -> None:
        self._write_theme(_NP_THEME)
        self._install_wrapper()
        stale = self._find_stale_claude_pids()
        if stale:
            # The wrapper engages at exec() time. Sessions launched while
            # nightpanel-active didn't exist kept their inherited COLORTERM,
            # so they're still rendering bgcolors via the full RGB palette
            # instead of routing through alacritty's NP-tinted 16-color
            # palette. The only fix is for the user to relaunch claude.
            # Log-only: the journal is the durable signal. The desktop
            # notification was removed — it fired on every toggle-on for any
            # long-lived pre-wrapper session, spamming the message tray and the
            # lock screen. See _notify_stale's docstring.
            self._log.warning(
                "nightpanel: %d claude session(s) pre-date the wrapper "
                "(COLORTERM still set); user needs to relaunch: pids=%s",
                len(stale),
                stale,
            )

    def revert(self, snapshot: dict) -> None:
        for key, path in (("settings", self._settings), ("wrapper", self._wrapper)):
            captured = snapshot.get(key)
            if not captured:
                raise AdapterError(
                    f"{self.name}: no capture for {path}; refusing to revert on a guess"
                )
            self.restore(path, captured)

    def verify(self, expected: Literal["on", "off"]) -> bool:
        # Theme-on AND wrapper-installed both required for strict mode.
        theme_on = self._read_theme() == _NP_THEME
        wrapper_on = self._wrapper_installed()
        on = theme_on and wrapper_on
        return on if expected == "on" else not on

    # ── helpers ──
    def _read_settings(self) -> dict:
        try:
            return json.loads(self.read_text(self._settings))
        except json.JSONDecodeError as e:
            # A settings file we cannot parse is a file we cannot safely edit.
            # Returning {} here used to mean apply() overwrote the user's config
            # with a fresh one; snapshot() still holds the original bytes, so
            # revert can put it back exactly.
            raise AdapterError(f"{self.name}: {self._settings} is not valid JSON: {e}") from e

    def _read_theme(self) -> str | None:
        try:
            return self._read_settings().get("theme")
        except AdapterError:
            return None

    def _write_theme(self, value: str) -> None:
        data = self._read_settings()
        data["theme"] = value
        # Preserve key order + indentation matching the existing file style.
        self.write(self._settings, json.dumps(data, indent=2) + "\n")

    def _wrapper_installed(self) -> bool:
        """True iff the on-disk wrapper exactly matches this instance's script.

        Full-content comparison rather than marker-only: an earlier version of
        this adapter wrote a dumber passthrough script carrying the same marker,
        and a marker check left that stale script in place after the
        COLORTERM-stripping rewrite. Comparing content makes every wrapper change
        self-healing on the next apply().
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
        versions = self._real_dir
        return f"""\
#!/usr/bin/env bash
{marker}
# nightpanel-aware Claude Code wrapper.
# When nightpanel is active, strips COLORTERM so Claude Code falls back to
# 16-color rendering — every escape flows through the terminal ANSI palette
# (which nightpanel paints in strict green/amber/red). When nightpanel is
# off, the wrapper is a transparent passthrough. Generated by
# nightpanel.adapters.claude_code — regenerate by calling apply().

REAL=$(ls -1 "{versions}"/* 2>/dev/null | sort -V | tail -1)
if [ -z "$REAL" ] || [ ! -x "$REAL" ]; then
    echo "claude: real binary not found under {versions}" >&2
    exit 127
fi

if [ -e "{active}" ]; then
    exec env -u COLORTERM "$REAL" "$@"
else
    exec "$REAL" "$@"
fi
"""

    def _find_stale_claude_pids(self, proc_root: Path = Path("/proc")) -> list[int]:
        """Return PIDs of running claude sessions still holding COLORTERM.

        Scans /proc for processes whose cmdline points at the real claude
        binary under ``~/.local/share/claude/versions/`` and whose environ
        still has a ``COLORTERM=`` entry. Those sessions launched before the
        wrapper had a sentinel to check, so the truecolor-strip never engaged
        and they're rendering colors outside the NP palette.
        """
        stale: list[int] = []
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            return stale
        versions_marker = str(self._real_dir) + "/"
        for pid_dir in entries:
            if not pid_dir.name.isdigit():
                continue
            try:
                cmdline = (pid_dir / "cmdline").read_bytes()
                if versions_marker.encode() not in cmdline:
                    continue
                environ = (pid_dir / "environ").read_bytes()
                if b"COLORTERM=" in environ:
                    stale.append(int(pid_dir.name))
            except OSError:
                continue
        return stale

    def _install_wrapper(self) -> None:
        """Replace ``~/.local/bin/claude`` with the NP wrapper script.

        Idempotent — does nothing if our wrapper is already in place. The real
        binary keeps living in ``versions/``.
        """
        if not self._real_dir.exists():
            self._log.debug("nightpanel: claude versions dir not found, skipping wrapper")
            return
        if self._wrapper_installed():
            return
        self.write(self._wrapper, self._wrapper_script())
        try:
            self._wrapper.chmod(0o755)
        except OSError as e:
            raise AdapterError(f"{self.name}: could not make {self._wrapper} executable: {e}") from e

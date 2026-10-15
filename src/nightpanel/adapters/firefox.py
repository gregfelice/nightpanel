# SPDX-License-Identifier: GPL-3.0-or-later
"""Firefox adapter — writes the command JSON the native host polls, plus
manages userChrome.css and the user.js pref block (the chrome styling and
session prefs absorbed from the retired slimfox project).

Revert restores userChrome.css and user.js byte-for-byte. The previous version
returned an empty snapshot and left both files in place with a comment saying
chrome styling "is not a per-toggle concern" — but apply() writes them, so they
are user state this adapter mutates and must put back. The command file is our
own artifact and is not restorable.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path
from typing import Literal

from ..palette import Palette
from ..renderers import firefox_chrome as _chrome_renderer
from .base import Adapter, AdapterError

_CHROME_PREF = "toolkit.legacyUserProfileCustomizations.stylesheets"

_MARK_BEGIN = "/* >>> nightpanel managed — do not edit between markers <<< */"
_MARK_END = "/* <<< nightpanel managed end >>> */"

# Prefs the chrome CSS depends on. The userChrome rules keep the vertical-tabs
# rail visible, which only exists when the sidebar revamp is on. The session
# prefs below came from slimfox's user.js block, absorbed here 2026-08-30 when
# slimfox was uninstalled — nightpanel is now the sole owner of all of them.
_MANAGED_PREFS = f"""\
{_MARK_BEGIN}
user_pref("{_CHROME_PREF}", true);
// tab management: native vertical-tabs rail, expanded via Ctrl+Alt+Z.
// expandOnHover is OFF on purpose: it is mutually exclusive with the
// manual/keyboard expand toggle, and enabling it makes Ctrl+Alt+Z a no-op.
user_pref("sidebar.revamp", true);
user_pref("sidebar.verticalTabs", true);
// hide-sidebar + verticalTabs = launcher hidden at startup, summoned with
// Ctrl+Alt+Z. Under this value Firefox's toggleSidebarKb handler takes the
// show/hide branch rather than the expand/collapse one, so the key is a
// visibility toggle. always-show would pin the rail on screen permanently.
user_pref("sidebar.visibility", "hide-sidebar");
user_pref("sidebar.expandOnHover", false);
// always restore the previous window/tab set on launch. Native tab groups
// live in the session store, so this is what makes them persist.
user_pref("browser.startup.page", 3);
// Ctrl+Tab = most-recently-used thumbnail switcher (reveals no chrome)
user_pref("browser.ctrlTab.sortByRecentlyUsed", true);
// tighten crash-safety of tab groups (default 15000ms -> 5000ms)
user_pref("browser.sessionstore.interval", 5000);
{_MARK_END}
"""


def _strip_managed_block(text: str) -> str:
    """Drop every prior nightpanel block, keeping the rest of user.js intact."""
    out, skip = [], False
    for line in text.splitlines(keepends=True):
        if _MARK_BEGIN in line:
            skip = True
        if not skip:
            out.append(line)
        if _MARK_END in line:
            skip = False
    return "".join(out)


def find_default_profile(ff_root: Path) -> Path | None:
    """Locate the default Firefox profile dir from ``profiles.ini``.

    Priority (matches what Firefox itself picks):
      1. ``[Install*]`` section's ``Default=`` value (the active install's profile)
      2. First ``[Profile*]`` section with ``Default=1``
      3. First ``[Profile*]`` section in file order

    Returns the absolute Path, or None if no profile is discoverable.
    """
    ini = ff_root / "profiles.ini"
    if not ini.exists():
        return None
    cp = configparser.ConfigParser()
    try:
        cp.read(ini)
    except configparser.Error as e:
        # A profiles.ini that cannot be parsed is a real failure: guessing which
        # profile is the default would write into the wrong profile.
        raise AdapterError(f"firefox: profiles.ini unreadable: {e}") from e

    def _resolve(rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        return p if p.is_absolute() else ff_root / p

    for section in cp.sections():
        if section.startswith("Install") and cp[section].get("Default"):
            return _resolve(cp[section]["Default"])

    profiles = [s for s in cp.sections() if s.startswith("Profile")]
    for section in profiles:
        if cp[section].get("Default") == "1" and cp[section].get("Path"):
            return _resolve(cp[section]["Path"])
    for section in profiles:
        if cp[section].get("Path"):
            return _resolve(cp[section]["Path"])
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class FirefoxAdapter(Adapter):
    name = "firefox"

    @property
    def _command_file(self) -> Path:
        return self.paths.nightpanel / "np-command.json"

    @property
    def _ff_root(self) -> Path:
        return self.paths.home / ".mozilla" / "firefox"

    def installed(self) -> bool:
        # The extension may be installed even when firefox isn't running, and
        # the command file is harmless if nothing reads it. Always engage.
        return True

    def restorable_paths(self) -> list[Path]:
        profile = find_default_profile(self._ff_root)
        if profile is None or not profile.exists():
            return []
        return [profile / "chrome" / "userChrome.css", profile / "user.js"]

    def snapshot(self) -> dict:
        # The profile is part of the capture: without it, an empty `files` could
        # mean "no profile existed" or "we never looked", and those are different
        # things to a revert.
        profile = find_default_profile(self._ff_root)
        return {
            "profile": str(profile) if profile else None,
            "files": {str(p): self.capture(p) for p in self.restorable_paths()},
        }

    def apply(self, palette: Palette) -> None:
        self._write_command(
            {
                "action": "apply",
                "brightness": self._brightness(),
                "videoBrightness": self._video_brightness(),
            }
        )
        # Re-sync userChrome.css from the palette on every apply so palette
        # edits propagate. Firefox requires a full restart to pick the change
        # up (it doesn't hot-reload chrome.css), but the file is correct at
        # rest. Idempotent.
        self._install_user_chrome(palette)

    def revert(self, snapshot: dict) -> None:
        self._write_command({"action": "revert", "brightness": 0.9})
        if not snapshot:
            raise AdapterError(f"{self.name}: no capture; refusing to revert on a guess")
        files = snapshot.get("files") or {}
        if not files:
            # The capture exists and recorded no profile: nothing was written, so
            # there is nothing to put back. That is an answer, not a guess.
            return
        for path in [Path(p) for p in files]:
            self.restore(path, files[str(path)])

    def verify(self, expected: Literal["on", "off"]) -> bool:
        try:
            cmd = json.loads(self.read_text(self._command_file))
        except (OSError, json.JSONDecodeError):
            return expected == "off"
        on = cmd.get("action") == "apply"
        return on if expected == "on" else not on

    # ── helpers ──
    def _brightness(self) -> float:
        """Read brightness from gsettings; clamp to a sane range."""
        raw = self.run(["gsettings", "get", "io.github.gregfelice.Nightpanel", "theme-brightness"])
        try:
            return _clamp(float(raw), 0.3, 1.5)
        except (TypeError, ValueError):
            return 0.9

    def _video_brightness(self) -> float:
        """Read the dedicated <video> brightness from gsettings; clamp to range.

        Read through the Runner rather than in-process Gio.Settings.get_double,
        which aborts the process on a missing key. If the schema predates this
        key the read returns non-zero and we fall back to 1.0 (untouched)."""
        raw = self.run(["gsettings", "get", "io.github.gregfelice.Nightpanel", "video-brightness"])
        try:
            return _clamp(float(raw), 0.1, 1.0)
        except (TypeError, ValueError):
            return 1.0

    def _write_command(self, payload: dict) -> None:
        self.write(self._command_file, json.dumps(payload))

    def _install_user_chrome(self, palette: Palette) -> None:
        """Write userChrome.css from the palette and ensure the FF pref that
        lets userChrome load is set. Replaces the old slimfox installer."""
        profile = find_default_profile(self._ff_root)
        if profile is None or not profile.exists():
            self._log.debug("nightpanel: no Firefox profile found, skipping userChrome")
            return
        user_chrome = profile / "chrome" / "userChrome.css"
        user_js = profile / "user.js"
        self.write(user_chrome, _chrome_renderer.render(palette))
        existing = self.read_text(user_js) if user_js.exists() else ""
        desired = _strip_managed_block(existing).rstrip("\n") + "\n" + _MANAGED_PREFS
        self.write(user_js, desired)

# SPDX-License-Identifier: GPL-3.0-or-later
"""NightpanelOrchestrator — drives the registered Adapters on apply / revert.

Each Adapter (alacritty, tmux, nvim, gnome, firefox …) owns its own state
shape. The orchestrator just iterates the list, namespacing snapshots under
the adapter's name in a single JSON file. Adding a new tool means writing
one Adapter subclass and appending it to ``self.adapters``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from subprocess import CompletedProcess
from typing import Literal

from ..adapters import (
    Adapter,
    AlacrittyAdapter,
    ClaudeCodeAdapter,
    EmacsAdapter,
    FirefoxAdapter,
    GeminiCliAdapter,
    GnomeAdapter,
    GwsAdapter,
    NvimAdapter,
    TmuxAdapter,
)
from ..adapters.base import AdapterError, AdapterNotLive
from ..adapters.firefox import find_default_profile as _find_ff_profile
from ..palette import NIGHTPANEL, Palette
from ..paths import Paths
from ..runner import Runner, SubprocessRunner

_LOG = logging.getLogger(__name__)


class OrchestratorError(Exception):
    """A failure in the orchestrator's own bookkeeping: the state file, ACTIVE_FILE.

    Adapters raise AdapterError for their own failures, and the battery refuses an
    adapter that logs a warning and returns. The orchestrator had been doing exactly
    that with the state file — a failed write logged, returned, and then ACTIVE_FILE
    was touched, so the state machine claimed the world was on with no baseline
    behind it. Same rule, applied to the thing doing the enforcing.
    """


# Source files that ship with the package — these are not user paths, so they
# stay module-level. Everything the orchestrator *writes* is resolved through
# the injected Paths (see paths.py): the import-time Path.home() constants here
# were the same bug class the adapters were rebuilt to remove, and they could
# disagree with the adapters about where the config dir is.
_NP_HOST_SRC = Path(__file__).parent / "np-host.py"
_EXT_SRC = Path(__file__).parent / "firefox-extension"
_EXT_ID = "nightpanel-bridge@nightpanel"

# Read-only system location, not a user path: the ghost-prune check looks here
# as well as in the user prefix. Nothing is written to it.
_SYSTEM_EXT_DIR = Path("/usr/share/gnome-shell/extensions")

# Security implication shown when the user requests Firefox bridge install.
_FF_CONSENT_TEXT = """\
Installing the nightpanel Firefox bridge requires lowering Firefox's
extension-signature requirement (xpinstall.signatures.required=false).

This is a global Firefox security setting. It applies to ALL extensions
in your profile — not just nightpanel's bridge. While it's set to false,
Firefox will install unsigned extensions, which weakens its security posture.

The bridge extension itself is unsigned because it isn't published on
Mozilla AMO yet. Until it's signed, this pref must stay flipped for the
bridge to load.

To opt in, pass confirmed=True to install_bridge(), e.g.:
    NightpanelOrchestrator().install_bridge(confirmed=True)

Default install does NOT touch Firefox."""


class NightpanelOrchestrator:
    """Toggles nightpanel by driving a list of Adapters.

    Call ``apply()`` to engage, ``revert()`` to disengage, ``verify(expected)``
    to confirm each adapter is in the expected state. Pass a custom Palette
    to ``apply()`` to test alternate color schemes.

    ``paths`` and ``runner`` are injected for the same reason the adapters take
    them: without injection the state file and ACTIVE_FILE live in the
    developer's home during tests, and the XDG layout is wrong by construction.
    """

    def __init__(
        self,
        palette: Palette = NIGHTPANEL,
        adapters: list[Adapter] | None = None,
        paths: Paths | None = None,
        runner: Runner | None = None,
    ):
        self.paths = paths or Paths.detect()
        self.runner = runner or SubprocessRunner()
        self.palette = palette
        self.adapters: list[Adapter] = (
            adapters
            if adapters is not None
            else [
                AlacrittyAdapter(paths=self.paths, runner=self.runner),
                TmuxAdapter(paths=self.paths, runner=self.runner),
                NvimAdapter(paths=self.paths, runner=self.runner),
                EmacsAdapter(paths=self.paths, runner=self.runner),
                ClaudeCodeAdapter(paths=self.paths, runner=self.runner),
                GeminiCliAdapter(paths=self.paths, runner=self.runner),
                GnomeAdapter(paths=self.paths, runner=self.runner),
                FirefoxAdapter(paths=self.paths, runner=self.runner),
                GwsAdapter(paths=self.paths, runner=self.runner),
            ]
        )

    # ── Resolved user paths ───────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self.paths.nightpanel / "nightpanel-state.json"

    @property
    def _active_file(self) -> Path:
        return self.paths.nightpanel / "nightpanel-active"

    @property
    def _np_command(self) -> Path:
        return self.paths.nightpanel / "np-command.json"

    @property
    def _np_host_dest(self) -> Path:
        return self.paths.nightpanel / "np-host.py"

    @property
    def _nm_manifest(self) -> Path:
        return self.paths.home / ".mozilla" / "native-messaging-hosts" / "nightpanel.json"

    @property
    def _ff_root(self) -> Path:
        return self.paths.home / ".mozilla" / "firefox"

    def _run(self, cmd: list[str]) -> CompletedProcess[str]:
        return self.runner.run(cmd)

    # ── Public API ────────────────────────────────────────────────

    def apply(self) -> dict[str, bool]:
        """Snapshot current state then apply nightpanel via every adapter.

        Returns a ``{adapter_name: success}`` dict. ACTIVE_FILE is only touched
        when every stateful adapter both captured and applied — otherwise the
        state machine claims "on" while some tools hold a baseline they cannot
        put back.
        """
        active = self._active_adapters()

        # Only snapshot if not already engaged — protects against double-apply
        # corrupting the saved baseline. Defense in depth: if ACTIVE_FILE went
        # missing while the world is still in nightpanel state (interrupted
        # revert, manual cleanup), trust the existing snapshot over the live
        # world, otherwise we'd capture the applied palette as the baseline.
        if not self._active_file.exists():
            verdict = {a.name: a.verify("on") for a in active}
            already_on = {k: v for k, v in verdict.items() if v}
            if already_on:
                _LOG.warning(
                    "nightpanel: apply() with no ACTIVE_FILE but adapters look "
                    "already-on (%s) — keeping existing snapshot",
                    already_on,
                )
                captured = self._load_state()
            else:
                captured = {}
                for a in active:
                    try:
                        captured[a.name] = a.snapshot()
                    except AdapterError as e:
                        # A half-collected snapshot is not a baseline. Record the
                        # gap explicitly so the apply loop below can refuse for
                        # that adapter instead of guessing on revert.
                        _LOG.error("nightpanel: %s capture failed: %s", a.name, e)
                        captured[a.name] = None
                self._save_state({k: v for k, v in captured.items() if v})
        else:
            captured = self._load_state()

        outcomes: dict[str, bool] = {}
        for a in active:
            if a.stateful and captured.get(a.name) is None:
                # No baseline for this adapter: applying would create state we
                # have no record of, and revert would then have to guess.
                outcomes[a.name] = False
                continue
            try:
                a.apply(self.palette)
                outcomes[a.name] = True
            except AdapterNotLive as e:
                # The tool is not running. The file state is correct at rest and
                # the tool picks it up on next start — an outcome, not a success.
                _LOG.info("nightpanel: %s not live: %s", a.name, e)
                outcomes[a.name] = False
            except AdapterError as e:
                _LOG.error("nightpanel: %s apply failed: %s", a.name, e)
                outcomes[a.name] = False

        stateful_failed = [a.name for a in active if a.stateful and not outcomes.get(a.name)]
        if not stateful_failed and any(outcomes.values()):
            self._active_file.parent.mkdir(parents=True, exist_ok=True)
            self._active_file.touch()
        else:
            _LOG.error(
                "nightpanel: not marking ACTIVE_FILE — stateful adapters failed: %s. Outcomes: %s",
                stateful_failed,
                outcomes,
            )
        return outcomes

    def revert(self) -> dict[str, bool]:
        """Restore every adapter to its pre-apply state. Returns outcomes.

        ACTIVE_FILE is unlinked BEFORE the adapter reverts run, not after.
        Reason: adapter reverts take real time (gsettings round-trips, file
        writes, daemon round-trips), and during that window any incoming
        ``update_brightness`` call would see ACTIVE_FILE still present and
        write ``{"action":"apply", ...}`` to np-command.json — re-engaging
        Firefox while the rest of the world is mid-revert. The visible
        symptom was a "goes bright for a sec, then snaps back to dark"
        cycle when toggling off via the panel button while the config app
        was open emitting brightness-slider events on GTK re-layout.
        Unlinking first closes the race.
        """
        self._active_file.unlink(missing_ok=True)
        state = self._load_state()
        outcomes: dict[str, bool] = {}
        for a in self._active_adapters():
            try:
                a.revert(state.get(a.name, {}))
                outcomes[a.name] = True
            except AdapterNotLive as e:
                _LOG.info("nightpanel: %s not live during revert: %s", a.name, e)
                outcomes[a.name] = False
            except AdapterError as e:
                _LOG.error("nightpanel: %s revert failed: %s", a.name, e)
                outcomes[a.name] = False
        return outcomes

    def verify(self, expected: Literal["on", "off"]) -> dict[str, bool]:
        """Return ``{adapter_name: matches_expected}`` for every active adapter."""
        return {a.name: a.verify(expected) for a in self._active_adapters()}

    def is_active(self) -> bool:
        """Whether nightpanel is currently engaged system-wide.

        Source of truth = the ACTIVE_FILE sentinel, written by apply() and
        removed by revert(). Callers use this to decide whether to *resume*
        the system theme (e.g. re-apply on launch) — distinct from the in-app
        GTK theme flag, which only styles the config window itself.
        """
        return self._active_file.exists()

    _BRIGHTNESS_MIN_INTERVAL = 0.1  # seconds; rate-limit FF command writes to 10/s

    def update_brightness(self, brightness: float) -> None:
        """Push a brightness update to the Firefox extension.

        Rate-limited via _BRIGHTNESS_MIN_INTERVAL — without this, every
        Gtk.Scale value-changed signal (which can arrive 30+ times per
        second during drag, or from spurious scroll-wheel events
        passing through the parent ActionRow) writes np-command.json,
        which the native messaging host then forwards to Firefox at
        500ms cadence. The result is observable as a visible
        "cycling" of page brightness when the slider gets even a hint
        of wheel input from anywhere on its surrounding row.
        """
        if not self._active_file.exists():
            return
        import time

        now = time.monotonic()
        if now - getattr(self, "_last_brightness_write", 0.0) < self._BRIGHTNESS_MIN_INTERVAL:
            return
        self._last_brightness_write = now
        try:
            self._np_command.parent.mkdir(parents=True, exist_ok=True)
            self._np_command.write_text(json.dumps({"action": "apply", "brightness": brightness}))
        except OSError as e:
            _LOG.warning("nightpanel: brightness update failed: %s", e)

    def update_video_brightness(self, video_brightness: float) -> None:
        """Push a <video> brightness update to the Firefox extension.

        Mirrors update_brightness for the dedicated "video brightness" slider.
        Writes only the videoBrightness field — the extension keeps the last
        page-brightness value (keep-last per field), so the two sliders don't
        clobber each other. Uses its OWN rate-limit timestamp: sharing
        _last_brightness_write would let a drag on one slider suppress writes
        from the other.
        """
        if not self._active_file.exists():
            return
        import time

        now = time.monotonic()
        if now - getattr(self, "_last_video_brightness_write", 0.0) < self._BRIGHTNESS_MIN_INTERVAL:
            return
        self._last_video_brightness_write = now
        try:
            self._np_command.parent.mkdir(parents=True, exist_ok=True)
            self._np_command.write_text(
                json.dumps({"action": "apply", "videoBrightness": video_brightness})
            )
        except OSError as e:
            _LOG.warning("nightpanel: video brightness update failed: %s", e)

    # ── Install (one-time setup, not part of apply/revert cycle) ──

    def install_gnome_extension(self) -> bool:
        """Install + enable the GNOME Shell panel extension. Also prunes
        ghost entries from the enabled-extensions gsettings list.

        Ghost entries (enabled UUIDs whose dirs are missing) accumulate
        after renames or uninstalls and can prevent newer extensions from
        loading. The prune is cheap (one gsettings read + a few stats) and
        defensive — runs on every install_gnome_extension() call.

        Returns True if anything changed (shell refresh needed).
        """
        changed = False
        ext_src = Path(__file__).parent.parent / "shell-extension" / "nightpanel@nightpanel"
        ext_dest = self.paths.data / "gnome-shell" / "extensions" / "nightpanel@nightpanel"
        try:
            if ext_src.exists() and ext_src != ext_dest:
                ext_dest.mkdir(parents=True, exist_ok=True)
                for f in ext_src.iterdir():
                    dst = ext_dest / f.name
                    if not dst.exists() or dst.read_bytes() != f.read_bytes():
                        shutil.copy2(f, dst)
                        changed = True
            r = self._run(["gnome-extensions", "enable", "nightpanel@nightpanel"])
            if r.returncode != 0:
                _LOG.warning("nightpanel: gnome-extensions enable: %s", r.stderr.strip())
            else:
                changed = True
        except Exception as e:
            _LOG.warning("nightpanel: GNOME extension install failed: %s", e)
        if self._prune_ghost_extensions() > 0:
            changed = True
        return changed

    def _prune_ghost_extensions(self) -> int:
        """Remove enabled-extensions entries that point at missing dirs.

        Walks the gsettings ``org.gnome.shell enabled-extensions`` list,
        drops any UUID whose extension dir exists in neither the user
        prefix nor the system prefix, and writes the cleaned list back.
        Returns the number of ghosts removed.
        """
        try:
            r = self._run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"])
            if r.returncode != 0:
                return 0
            raw = r.stdout.strip().strip("[]")
            exts = [e.strip().strip("'") for e in raw.split(",") if e.strip()]
        except Exception as e:
            _LOG.debug("nightpanel: prune read failed: %s", e)
            return 0

        home_ext = self.paths.data / "gnome-shell" / "extensions"
        sys_ext = _SYSTEM_EXT_DIR

        keep, ghosts = [], []
        for ext in exts:
            if not ext:
                continue
            if (home_ext / ext).is_dir() or (sys_ext / ext).is_dir():
                keep.append(ext)
            else:
                ghosts.append(ext)

        if not ghosts:
            return 0

        new_list = "[" + ", ".join(f"'{e}'" for e in keep) + "]"
        try:
            r = self._run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", new_list])
            if r.returncode != 0:
                # The ghosts are still in the list; reporting the prune as done
                # would be the same lie rule #1 forbids in the adapters.
                _LOG.error(
                    "nightpanel: prune write failed: %s", r.stderr.strip() or "non-zero exit"
                )
                return 0
            _LOG.info(
                "nightpanel: pruned %d ghost extension(s): %s", len(ghosts), ", ".join(ghosts)
            )
        except Exception as e:
            _LOG.error("nightpanel: prune write failed: %s", e)
            return 0
        return len(ghosts)

    def install_bridge(self, *, confirmed: bool = False) -> bool:
        """Install Firefox native-messaging host + companion extension.

        Requires explicit ``confirmed=True`` because the bridge install lowers
        Firefox's global ``xpinstall.signatures.required`` pref. The caller
        (CLI handler, Setup-panel button) is responsible for presenting the
        security implication and obtaining user consent before passing True.

        Idempotent. Returns True if anything changed (Firefox restart needed).
        Raises ConsentRequired if ``confirmed`` is not True.
        """
        if not confirmed:
            raise ConsentRequired(_FF_CONSENT_TEXT)
        if _find_ff_profile(self._ff_root) is None:
            _LOG.warning("nightpanel: no Firefox profile found; bridge not installed")
            return False
        return self._install_native_host() | self._install_extension()

    # ── State persistence ────────────────────────────────────────

    def _save_state(self, state: dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._state_path.write_text(json.dumps(state, indent=2))
        except OSError as e:
            raise OrchestratorError(f"could not save state: {e}") from e

    def _load_state(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text())
        except OSError as e:
            raise OrchestratorError(f"could not read state: {e}") from e
        except json.JSONDecodeError as e:
            # A truncated state file is not the same as an absent one. Returning {}
            # here made every adapter report "no capture", which is true but hides the
            # cause: the journal showed nine failures for one damaged file.
            raise OrchestratorError(f"state file is corrupt: {e}") from e

    def _active_adapters(self):
        return [a for a in self.adapters if a.installed()]

    # ── Firefox install helpers ──

    def _install_native_host(self) -> bool:
        try:
            self._np_host_dest.parent.mkdir(parents=True, exist_ok=True)
            changed = (
                not self._np_host_dest.exists()
                or self._np_host_dest.read_bytes() != _NP_HOST_SRC.read_bytes()
            )
            if changed:
                shutil.copy2(_NP_HOST_SRC, self._np_host_dest)
                self._np_host_dest.chmod(0o755)
            manifest = {
                "name": "nightpanel",
                "description": "nightpanel bridge — connects nightpanel to Firefox",
                "path": str(self._np_host_dest),
                "type": "stdio",
                "allowed_extensions": [_EXT_ID],
            }
            manifest_json = json.dumps(manifest, indent=2)
            self._nm_manifest.parent.mkdir(parents=True, exist_ok=True)
            if not self._nm_manifest.exists() or self._nm_manifest.read_text() != manifest_json:
                self._nm_manifest.write_text(manifest_json)
                changed = True
            return changed
        except Exception as e:
            _LOG.warning("nightpanel: native host install failed: %s", e)
            return False

    def _install_extension(self) -> bool:
        profile = _find_ff_profile(self._ff_root)
        if profile is None:
            _LOG.warning("nightpanel: no Firefox profile discovered, skipping extension")
            return False
        try:
            ext_dest = profile / "extensions" / _EXT_ID
            if not _EXT_SRC.exists():
                _LOG.warning("nightpanel: extension source not found at %s", _EXT_SRC)
                return False
            changed = not ext_dest.exists()
            ext_dest.mkdir(parents=True, exist_ok=True)
            for src_file in _EXT_SRC.iterdir():
                dst = ext_dest / src_file.name
                if not dst.exists() or dst.read_bytes() != src_file.read_bytes():
                    shutil.copy2(src_file, dst)
                    changed = True
            changed |= self._ensure_user_js_pref(profile, "xpinstall.signatures.required", "false")
            return changed
        except Exception as e:
            _LOG.warning("nightpanel: extension install failed: %s", e)
            return False

    def _ensure_user_js_pref(self, profile: Path, key: str, value: str) -> bool:
        user_js = profile / "user.js"
        try:
            existing = user_js.read_text() if user_js.exists() else ""
            if f'user_pref("{key}"' in existing:
                return False
            with user_js.open("a") as f:
                f.write(f'\nuser_pref("{key}", {value});\n')
            return True
        except OSError as e:
            _LOG.warning("nightpanel: user.js update failed: %s", e)
            return False


class ConsentRequired(Exception):
    """Raised when an action requires explicit user consent and didn't get it.

    Carries a human-readable message describing the security/privacy
    implication and how to opt in.
    """

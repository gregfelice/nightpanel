# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter — the contract every nightpanel-aware tool implements.

The orchestrator iterates a list of Adapters, calling apply()/revert() to
toggle nightpanel on/off and verify() to confirm the state landed. Each
adapter owns its own state shape (returned from snapshot(), consumed by
revert()); the orchestrator just serializes it under the adapter's name.

To add a new tool (emacs, nautilus, eva, …):
    1. Subclass Adapter
    2. Implement snapshot/apply/revert/verify
    3. Declare restorable_paths() — the user files you write and put back
    4. Append an instance to NightpanelOrchestrator.adapters

The contract is enforced by tests/invariants/test_contract_invariants.py,
which runs the same battery against every adapter. A new adapter inherits the
battery automatically; that is the mechanism that keeps this class of bug out.

Two rules the battery enforces, and which the previous version of this file
did not:

* **An adapter must not swallow its own failures.** Logging a warning and
  returning records a success to the orchestrator, which then marks the system
  active while nothing changed. Adapters raise AdapterError instead.
* **An adapter that writes a user file must put it back.** `stateful` and
  `restorable_paths()` make that commitment explicit, so an adapter that
  cannot honour it has to say so in the contract rather than in a comment.
"""

from __future__ import annotations

import base64
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from subprocess import CompletedProcess
from typing import Literal

from ..palette import NIGHTPANEL, Palette
from ..paths import Paths
from ..runner import Runner, SubprocessRunner

_LOG = logging.getLogger(__name__)


class AdapterError(Exception):
    """A real failure — the adapter could not do what it claimed."""


class AdapterNotLive(Exception):
    """The tool was not running, so the live half of the apply could not happen.

    Not a failure: the file state is correct at rest and the tool picks it up on
    next start. It is still an *outcome*, and the orchestrator records it as one —
    never as success. That distinction is the whole of rule #1.
    """


class Adapter(ABC):
    """Contract for everything that participates in nightpanel toggling."""

    #: short slug used for state namespacing and logging (e.g. "alacritty")
    name: str = ""

    #: True when revert() depends on state captured by snapshot(). An adapter
    #: that genuinely has no prior state to restore must set this False and say
    #: why. The battery refuses to let a stateful adapter return an empty
    #: snapshot, which is how tmux and firefox were quietly doing hardcoded
    #: recovery instead of real restoration.
    stateful: bool = True

    def __init__(
        self,
        paths: Paths | None = None,
        runner: Runner | None = None,
        palette: Palette | None = None,
    ) -> None:
        if not getattr(self, "name", ""):
            raise AdapterError(f"{type(self).__name__} must define `name`")
        self.paths = paths or Paths.detect()
        self.runner = runner or SubprocessRunner()
        # verify() needs the palette to know what "on" looks like. Without it,
        # verify can only key off a sentinel or a hardcoded magic value, which
        # is exactly how the tmux adapter ended up testing for "#000000".
        self.palette = palette or NIGHTPANEL
        self._log = logging.getLogger(f"nightpanel.{self.name}")

    # ── Contract ──────────────────────────────────────────────────

    def installed(self) -> bool:
        """Is the target tool available? Adapters whose tool is missing are
        skipped by the orchestrator. Default: always True."""
        return True

    @abstractmethod
    def snapshot(self) -> dict:
        """Capture current state so revert() can restore it later.

        Returns an opaque dict; only this adapter interprets the keys. A
        stateful adapter must capture something observable.
        """

    @abstractmethod
    def apply(self, palette: Palette) -> None:
        """Apply the nightpanel scheme using colors from the palette.

        Raises AdapterError if the apply does not land.
        """

    @abstractmethod
    def revert(self, snapshot: dict) -> None:
        """Restore the state previously captured by snapshot().

        Raises AdapterError rather than guessing when the snapshot is empty or
        incomplete — deleting or blanking a user config file on a guess is the
        worst outcome this tool can produce.
        """

    @abstractmethod
    def verify(self, expected: Literal["on", "off"]) -> bool:
        """Cheaply check whether the live tool state matches expected."""

    def restorable_paths(self) -> list[Path]:
        """User files this adapter writes and commits to put back exactly.

        Files the adapter generates for its own use (rendered themes, wrappers
        that are self-aware at run time) are not user state and are not
        declared here — but the adapter must say so, which is why this method
        exists at all.
        """
        return []

    def own_paths(self) -> list[Path]:
        """Files that are ours, not the user's: regenerated on every apply, nothing to put back.

        Declared rather than commented, so the battery can check that every file an
        adapter writes is either restorable or explicitly ours. A file that is neither is
        a user file written without a capture that could put it back.
        """
        return []

    # ── Shared helpers ────────────────────────────────────────────

    def capture(self, path: Path) -> dict:
        """Record a file's content *and existence*.

        `existed` is recorded explicitly. A revert that receives a capture
        without it cannot tell "the file did not exist before us" from "we
        never looked", and guessing either way can destroy user config.

        A symlink is recorded as a link, not as the content behind it. The
        claude_code and gemini_cli adapters replace the installer's symlink
        with a wrapper script, so "put it back byte-for-byte" has to mean the
        link itself — writing the target's content as a regular file would
        leave a file where a symlink used to be.

        `mode` is recorded because the wrapper scripts are executable: a
        restore that writes the right bytes with the wrong mode leaves a file
        that cannot be run.

        Content is read as bytes. A config file that is not valid UTF-8 is not
        an error, and `UnicodeDecodeError` is not an `OSError`, so reading as
        text would let it escape this method as an unexpected exception. Text
        is stored as text when it decodes (the state file stays readable);
        anything else is stored base64 with `binary: True`.
        """
        try:
            if not path.exists() and not path.is_symlink():
                return {
                    "path": str(path),
                    "existed": False,
                    "content": None,
                    "link_to": None,
                    "mode": None,
                }
            if path.is_symlink():
                return {
                    "path": str(path),
                    "existed": True,
                    "content": None,
                    "link_to": os.readlink(path),
                    "mode": None,  # a symlink's own mode is always 0o777
                }
            raw = path.read_bytes()
            try:
                content: str | None = raw.decode()
                binary = False
            except UnicodeDecodeError:
                content = base64.b64encode(raw).decode("ascii")
                binary = True
            return {
                "path": str(path),
                "existed": True,
                "content": content,
                "binary": binary,
                "link_to": None,
                "mode": path.stat().st_mode & 0o777,
            }
        except OSError as e:
            raise AdapterError(f"{self.name}: could not read {path}: {e}") from e

    def restore(self, path: Path, captured: dict) -> None:
        """Put a captured file back byte-for-byte, or remove it if it did not
        exist before us.

        Refuses on an empty or incomplete capture rather than guessing.
        """
        if not captured or "existed" not in captured:
            raise AdapterError(f"{self.name}: no capture for {path}; refusing to revert on a guess")
        # The capture records where it came from. If the caller asks for a
        # different file, the snapshot is not the one this path belongs to —
        # most likely a state file written by an older version or a renamed
        # adapter. Restoring the wrong capture onto the wrong file is the same
        # class of damage as guessing.
        recorded = captured.get("path")
        if recorded and recorded != str(path):
            raise AdapterError(
                f"{self.name}: capture is for {recorded}, not {path}; refusing to revert"
            )
        if captured["existed"] and not captured.get("link_to") and captured.get("content") is None:
            raise AdapterError(
                f"{self.name}: capture for {path} claims the file existed but has no content"
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not captured["existed"]:
                path.unlink(missing_ok=True)
            elif captured.get("link_to"):
                path.unlink(missing_ok=True)
                os.symlink(captured["link_to"], path)
            else:
                content = captured["content"]
                raw = base64.b64decode(content) if captured.get("binary") else content.encode()
                path.write_bytes(raw)
                if captured.get("mode") is not None:
                    path.chmod(captured["mode"])
        except OSError as e:
            raise AdapterError(f"{self.name}: could not restore {path}: {e}") from e

    def read_text(self, path: Path) -> str:
        """Read a file as text, raising rather than crashing.

        `Path.read_text()` raises `UnicodeDecodeError` on a binary file, which is a
        `ValueError` and escapes the `except OSError` blocks in every adapter — one
        unreadable file aborts the toggle for all the other tools. A file we cannot
        read is a file we cannot safely edit, which is an `AdapterError`, not a crash.

        Adapters read through this (or `capture()`), never through `Path.read_text()`
        directly; the battery's static rule enforces that.

        Absence is not a read failure and is not converted into one: every adapter treats
        a missing file as empty state, and turning `FileNotFoundError` into `AdapterError`
        means a fresh profile raises instead of being created. Callers that may see a
        missing file must say so with an existence check, and `OSError` is left to
        propagate so their `except OSError` stays live.
        """
        try:
            return path.read_text()
        except UnicodeDecodeError as e:
            raise AdapterError(f"{self.name}: could not read {path}: {e}") from e

    def write(self, path: Path, content: str) -> None:
        """Write a file, raising rather than logging.

        The orchestrator's success accounting depends on this. A write that logs
        a warning and returns is recorded as a success, and the state machine
        then lies about being on.

        A path that is a symlink is replaced, not written through. `capture()` records a
        link as a link with no content, so writing through it mutates a file the capture
        cannot put back — that is how claude_code's wrapper landed on the real binary under
        `versions/`. Whatever `restore()` puts back has to be what `capture()` recorded, so
        the write stays at the recorded location.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            path.write_text(content)
        except OSError as e:
            raise AdapterError(f"{self.name}: could not write {path}: {e}") from e

    def run(self, cmd: list[str]) -> str | None:
        """Run a command and return its stdout; None if it exited non-zero.

        Use this to *read* live state, and use the result. For the mutation
        itself use `run_checked` (must land) or `run_optional` (may fail because
        the tool is not running). A `self.run(...)` whose result is thrown away is
        a failure recorded as a success, and the battery refuses it.
        """
        r = self._spawn(cmd)
        return r.stdout.strip() if r.returncode == 0 else None

    def run_checked(self, cmd: list[str]) -> str:
        """Run a command that must land. Raises AdapterError if it does not.

        Use this for the mutation itself. A mutation that fails and is recorded as
        success is the lie this contract exists to prevent.
        """
        r = self._spawn(cmd)
        if r.returncode != 0:
            raise AdapterError(f"{self.name}: {cmd[0]} failed: {r.stderr.strip() or 'exit code'}")
        return r.stdout.strip()

    def run_optional(self, cmd: list[str], why: str) -> str:
        """Run a mutation that can legitimately fail because the tool is not running.

        `why` is required: it is the sentence explaining why a non-zero exit here
        is not a failure ("no tmux server is running; the overlay is correct at
        rest"). A swallowed failure and an optional failure look identical in the
        code, so the difference has to be stated at the call site — and the outcome
        still has to reach the caller, which is why this raises `AdapterNotLive`
        instead of returning quietly.
        """
        r = self._spawn(cmd)
        if r.returncode != 0:
            raise AdapterNotLive(f"{self.name}: {why} ({r.stderr.strip() or 'non-zero exit'})")
        return r.stdout.strip()

    def _spawn(self, cmd: list[str]) -> CompletedProcess[str]:
        try:
            return self.runner.run(cmd)
        except Exception as e:
            raise AdapterError(f"{self.name}: {cmd[0]} could not run: {e}") from e

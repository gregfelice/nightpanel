# SPDX-License-Identifier: GPL-3.0-or-later
"""Runner — the single place adapters shell out through.

Injected so the live-session half of the contract (tmux source-file,
emacsclient, nvim --remote, gsettings) is exercisable without a live session.
Every adapter previously had its own copy of ``_run``; that duplication is why
the same timeout, the same error swallowing, and the same bug appeared in five
files.

The test guard in ``tests/conftest.py`` still applies: a real
``SubprocessRunner`` calls ``subprocess.run``, which the autouse guard wraps to
refuse pkexec/sudo/doas.
"""

from __future__ import annotations

import subprocess
from subprocess import CompletedProcess
from typing import Protocol


class Runner(Protocol):
    """Execute a command and return its result."""

    def run(self, cmd: list[str]) -> CompletedProcess[str]:
        """Run ``cmd``, capturing stdout/stderr as text."""
        ...


class SubprocessRunner:
    """Production Runner. Raises on spawn failure; a non-zero exit is a result,
    not an exception, so callers can distinguish "command failed" from "command
    could not run at all"."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def run(self, cmd: list[str]) -> CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)


class RecordingRunner:
    """Test Runner: records commands, answers from a canned table.

    Two ways to be unanswered, and the difference matters:

    - default: ``returncode 0`` with empty output — the tool is running and
      answered with nothing.
    - ``no_session=True``: any command not in ``answers`` or ``prefix`` exits 1,
      which is what "the tool is not running" actually looks like. An adapter
      that treats these two as the same state cannot pass a test that separates
      them, which is the point of the option.

    ``fail`` is the failure-injection set: commands in it return non-zero, which
    is how the battery proves an adapter reports a failed mutation instead of
    logging it and returning. ``fail_all`` is the blunt version — every command
    fails, which is the "the tool is running and the command did not land" case.

    ``prefix`` answers by the first k tokens, so a whole family of commands
    (``tmux show -gv <any option>``) can be answered without enumerating them.
    """

    def __init__(
        self,
        answers: dict[tuple[str, ...], str] | None = None,
        fail: set[tuple[str, ...]] | None = None,
        prefix: dict[tuple[str, ...], str] | None = None,
        fail_all: bool = False,
        no_session: bool = False,
    ) -> None:
        self.answers = answers or {}
        self.prefix = prefix or {}
        self.fail = fail or set()
        self.fail_all = fail_all
        self.no_session = no_session
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str]) -> CompletedProcess[str]:
        self.calls.append(list(cmd))
        key = tuple(cmd)
        answered = key in self.answers or any(
            key[: len(pattern)] == pattern for pattern in self.prefix
        )
        if self.fail_all or key in self.fail or (self.no_session and not answered):
            return CompletedProcess(cmd, 1, "", f"{cmd[0]}: not running")
        for pattern, value in self.prefix.items():
            if key[: len(pattern)] == pattern:
                return CompletedProcess(cmd, 0, value, "")
        return CompletedProcess(cmd, 0, self.answers.get(key, ""), "")

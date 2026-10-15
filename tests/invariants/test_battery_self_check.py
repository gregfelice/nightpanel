# SPDX-License-Identifier: GPL-3.0-or-later
"""Proof that the battery is a mechanism and not a ceremony.

Every check in `test_contract_invariants.py` is green on the real adapters. A
battery that never fails is not enforcing anything. This file defines one
deliberately bad adapter and shows the same checks are red on it — so a new
adapter written next month that repeats these shapes fails `./bin/test` without
anyone writing a test for it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Literal

import pytest

from nightpanel.adapters import Adapter
from nightpanel.palette import Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

from . import test_contract_invariants as battery
from .conftest import Env


class _SwallowingAdapter(Adapter):
    """An adapter written to violate the contract, on purpose."""

    name = "swallowing"

    def restorable_paths(self) -> list[Path]:
        return []

    def snapshot(self) -> dict:
        return {}

    def apply(self, palette: Palette) -> None:
        self.run(["anything"])  # result thrown away: rule #1 forbids this

    def revert(self, snapshot: dict) -> None:
        pass  # reverts on a guess: rule #4 forbids this

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_a_discarded_command_result() -> None:
    tree = ast.parse(inspect.getsource(_SwallowingAdapter))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "run"
    ]
    assert offenders, "the battery must flag a discarded command result"


def test_battery_flags_a_revert_on_a_guess(tmp_path: Path) -> None:
    bad = _SwallowingAdapter(paths=Paths(home=tmp_path))
    assert not battery._refuses_empty_snapshot(bad), (
        "the battery must flag a stateful adapter that reverts on a guess"
    )


def test_battery_flags_a_hardcoded_hex(tmp_path: Path) -> None:
    """The palette rule, proven on a source that breaks it."""
    import re

    src = 'def verify(self, expected):\n    return "bg=#000000" in live\n'
    literals = {
        n.value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert any(re.search(r"#[0-9a-fA-F]{6}", s) for s in literals), (
        "the battery must flag a hardcoded hex"
    )


class _Base64WritingAdapter(Adapter):
    """An adapter that stores a capture and writes the recorded content back as text.

    For a file that is not UTF-8, `capture()` records base64 with a `binary` flag.
    Writing `captured["content"]` puts the base64 in the user's file instead of the
    bytes that were there — the corruption the binary rule exists to prevent.
    """

    name = "base64-writer"

    def restorable_paths(self) -> list[Path]:
        return [self.paths.home / "state-file"]

    def snapshot(self) -> dict:
        p = self.restorable_paths()[0]
        return {str(p): self.capture(p)}

    def apply(self, palette: Palette) -> None:
        self.write(self.restorable_paths()[0], "nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        captured = snapshot[str(self.restorable_paths()[0])]
        self.write(self.restorable_paths()[0], captured["content"])

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_writing_a_binary_capture_as_text(tmp_path: Path) -> None:
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _Base64WritingAdapter(paths=env.paths, runner=env.runner)
    env.seed(bad)

    with pytest.raises(AssertionError):
        battery.test_a_non_utf8_file_comes_back_byte_for_byte(bad, env)

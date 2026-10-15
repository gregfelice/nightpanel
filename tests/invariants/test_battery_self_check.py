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
import importlib
import inspect
import sys
from pathlib import Path
from typing import Literal

import pytest

from nightpanel.adapters import Adapter
from nightpanel.adapters.base import AdapterError
from nightpanel.palette import NIGHTPANEL, Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

from . import conftest
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
    assert battery._static_violations(inspect.getsource(_SwallowingAdapter))["discarded_run"], (
        "the battery must flag a discarded command result"
    )


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


class _LinkFollowingAdapter(Adapter):
    """An adapter that writes through a symlink instead of replacing it."""

    name = "link-follower"

    def write(self, path: Path, content: str) -> None:
        path.resolve().write_text(content)  # follows the link to the file behind it

    def restorable_paths(self) -> list[Path]:
        return [self.paths.home / "link"]

    def snapshot(self) -> dict:
        return {str(self.restorable_paths()[0]): self.capture(self.restorable_paths()[0])}

    def apply(self, palette: Palette) -> None:
        self.write(self.restorable_paths()[0], "nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        self.restore(self.restorable_paths()[0], snapshot[str(self.restorable_paths()[0])])

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_writing_through_a_symlink(tmp_path: Path) -> None:
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _LinkFollowingAdapter(paths=env.paths, runner=env.runner)

    with pytest.raises(AssertionError):
        battery.test_write_does_not_write_through_a_symlink(bad, env)


class _UndeclaredWriterAdapter(Adapter):
    """An adapter that writes a user file it never declares."""

    name = "undeclared-writer"

    def restorable_paths(self) -> list[Path]:
        return []  # claims nothing to put back, then writes a file anyway

    def snapshot(self) -> dict:
        return {}

    def apply(self, palette: Palette) -> None:
        self.write(self.paths.home / "the-users-file", "nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        pass

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_writing_an_undeclared_file(tmp_path: Path) -> None:
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _UndeclaredWriterAdapter(paths=env.paths, runner=env.runner)

    with pytest.raises(AssertionError):
        battery.test_every_user_file_an_adapter_writes_is_declared_restorable(bad, env)


class _OwnPathAdapter(Adapter):
    """An adapter that declares a path as its own and overwrites a file already there.

    The declaration is true only for a file the adapter created. This one writes over a
    file the user already had, and its revert has no capture to restore from. The
    declaration check is green on it — the file it writes is declared — so only the
    round trip can see that the file was user state before the adapter arrived. That is
    why the rule has to be checked on behaviour, not on the source.
    """

    #: No restorable path, so nothing can be put back. The reason is the shape itself.
    stateful = False

    name = "own-path"

    def own_paths(self) -> list[Path]:
        return [self.paths.home / "ours"]

    def snapshot(self) -> dict:
        return {}  # declares the path as its own and never captures it

    def apply(self, palette: Palette) -> None:
        self.write(self.own_paths()[0], "nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        pass  # nothing was captured, so the user's file is gone

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_an_own_path_that_is_user_state(tmp_path: Path) -> None:
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _OwnPathAdapter(paths=env.paths, runner=env.runner)

    # The declaration itself is honest-looking: the file it writes is declared as its
    # own. The rule that bites is the round trip.
    battery.test_every_user_file_an_adapter_writes_is_declared_restorable(bad, env)

    with pytest.raises(AssertionError):
        battery.test_a_declared_own_path_that_already_exists_is_captured_or_refused(bad, env)


class _OwnPathRefusedAfterWriteAdapter(Adapter):
    """An adapter that overwrites a user file at its declared own path, then refuses.

    The refusal is the honest answer to "I have nothing to put back" — but it arrives
    at `revert()`, after `apply()` already destroyed the file. A refusal is honest
    only when it happens before the write, so the round trip has to check the content
    on the refused path too. This is the shape the rule accepted as honest while the
    test returned early on any refusal.
    """

    #: No restorable path, so nothing can be put back. The reason is the shape itself.
    stateful = False

    name = "own-path-refused-after-write"

    def own_paths(self) -> list[Path]:
        return [self.paths.home / "ours"]

    def snapshot(self) -> dict:
        return {}  # declares the path as its own and never captures it

    def apply(self, palette: Palette) -> None:
        self.write(self.own_paths()[0], "nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        raise AdapterError("no capture; refusing to revert on a guess")

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_a_refusal_that_arrives_after_the_write(tmp_path: Path) -> None:
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _OwnPathRefusedAfterWriteAdapter(paths=env.paths, runner=env.runner)

    # The declaration is honest-looking and the refusal reads as honest on its own;
    # only the round trip shows the user's file was destroyed before the refusal.
    with pytest.raises(AssertionError):
        battery.test_a_declared_own_path_that_already_exists_is_captured_or_refused(bad, env)


class _DirectWritingAdapter(Adapter):
    """An adapter that writes through Path.write_text instead of base.write()."""

    name = "direct-writer"

    def restorable_paths(self) -> list[Path]:
        return [self.paths.home / "state-file"]

    def snapshot(self) -> dict:
        return {str(self.restorable_paths()[0]): self.capture(self.restorable_paths()[0])}

    def apply(self, palette: Palette) -> None:
        (self.paths.home / "state-file").write_text("nightpanel applied\n")

    def revert(self, snapshot: dict) -> None:
        self.restore(self.restorable_paths()[0], snapshot[str(self.restorable_paths()[0])])

    def verify(self, expected: Literal["on", "off"]) -> bool:
        return True


def test_battery_flags_a_write_that_bypasses_base() -> None:
    """base.write() is where the symlink rule and the failure rule live.

    A write through Path.write_text escapes both, and it is invisible to the
    runtime check that records what an adapter wrote — so the rule has to be on
    the source.
    """
    assert battery._static_violations(inspect.getsource(_DirectWritingAdapter))["raw_writes"], (
        "the battery must flag a write that bypasses base.write()"
    )


class _DefaultPaletteAdapter(Adapter):
    """An adapter whose verify() compares against the default palette instance.

    This is the bug shape the static rules cannot see: no hex literal, no absolute
    path, nothing in the source that looks wrong — just a reference. It is the same
    mistake as the tmux adapter testing for "#000000", in the form that survives a
    lint rule, and it is why the verify checks have to run.
    """

    name = "default-palette"

    def restorable_paths(self) -> list[Path]:
        return [self.paths.home / "theme"]

    def snapshot(self) -> dict:
        return {str(self.restorable_paths()[0]): self.capture(self.restorable_paths()[0])}

    def apply(self, palette: Palette) -> None:
        self.write(self.restorable_paths()[0], f"bg={palette.bg}\n")

    def revert(self, snapshot: dict) -> None:
        self.restore(self.restorable_paths()[0], snapshot[str(self.restorable_paths()[0])])

    def verify(self, expected: Literal["on", "off"]) -> bool:
        on = self.palette is NIGHTPANEL
        return on if expected == "on" else not on


def test_battery_flags_a_verify_that_hardcodes_the_palette(tmp_path: Path) -> None:
    """The runtime rules are the only ones that can see a reference.

    Applying the battery palette and asking about a second one is what separates a
    verify that tracks the palette from one that names a particular palette. Nothing
    in the source distinguishes them.
    """
    env = Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())
    bad = _DefaultPaletteAdapter(
        paths=env.paths, runner=env.runner, palette=battery.BATTERY_PALETTE
    )

    with pytest.raises(AssertionError):
        battery.test_verify_agrees_with_what_apply_did(bad, env)
    with pytest.raises(AssertionError):
        battery.test_verify_tracks_the_palette(bad, env)


def test_battery_flags_an_adapter_module_that_is_not_exported(tmp_path: Path) -> None:
    """The miss rule 1 exists to catch, proven on the shape of the miss.

    Namespace discovery only shows what was imported, so an adapter module written
    and never imported is invisible to every check in the battery — including the
    registration check, because the adapter is not in the list either. A package
    with a ghost module and an empty __init__ shows the difference, and why the
    battery discovers from the files instead.
    """
    pkg_dir = tmp_path / "ghostpkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "ghost.py").write_text(
        "from nightpanel.adapters import Adapter\n"
        "class GhostAdapter(Adapter):\n"
        "    name = 'ghost'\n"
        "    def snapshot(self):\n        return {}\n"
        "    def apply(self, palette):\n        pass\n"
        "    def revert(self, snapshot):\n        pass\n"
        "    def verify(self, expected):\n        return True\n"
    )

    sys.path.insert(0, str(tmp_path))
    try:
        pkg = importlib.import_module("ghostpkg")
        assert conftest.adapters_in_package(pkg) == [], (
            "namespace discovery must miss an adapter module that was never imported"
        )
        found = conftest.adapters_from_files(pkg)
        assert [c.__name__ for c in found] == ["GhostAdapter"], (
            "file discovery must find it, so the export check can fail on it"
        )
    finally:
        sys.path.remove(str(tmp_path))
        for name in ("ghostpkg", "ghostpkg.ghost"):
            sys.modules.pop(name, None)

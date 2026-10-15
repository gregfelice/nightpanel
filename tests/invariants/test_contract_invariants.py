# SPDX-License-Identifier: GPL-3.0-or-later
"""The contract battery.

Every test here is parametrized over every Adapter subclass found in
``nightpanel.adapters``, so a new adapter inherits the whole battery without
anyone writing a test for it. That is the point: the rules in
``adapters/base.py`` are only real if a violation fails here.

The battery runs in a fixture root, never in the developer's home. Adapters are
constructed with injected ``Paths`` and a ``RecordingRunner``; the runner answers
from a canned table, so the "no live session" case is the normal one.

Run it:

    ./bin/test tests/invariants/
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from nightpanel.adapters import Adapter
from nightpanel.adapters.base import AdapterError, AdapterNotLive
from nightpanel.palette import Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner
from nightpanel.services.nightpanel_orchestrator import NightpanelOrchestrator

from .conftest import Env, all_adapters

ADAPTERS = all_adapters()

#: The palette the battery applies is deliberately NOT the default one. An
#: adapter that verifies against a hardcoded color instead of the palette it was
#: handed fails here.
BATTERY_PALETTE = Palette(name="battery", bg="#101010", bg_header="#101010", fg="#246424")

pytestmark = pytest.mark.parametrize(
    "adapter", ADAPTERS, ids=[a.name for a in ADAPTERS], indirect=True
)


def _snapshot_state(adapter: Adapter) -> dict[str, tuple]:
    """Record what the fixture looks like before nightpanel touches it."""
    out = {}
    for p in adapter.restorable_paths():
        if p.is_symlink():
            out[str(p)] = ("link", str(p.readlink()), None)
        elif p.exists():
            out[str(p)] = ("file", p.read_bytes(), p.stat().st_mode & 0o777)
        else:
            out[str(p)] = ("absent", None, None)
    return out


def _assert_unchanged(adapter: Adapter, before: dict[str, tuple]) -> None:
    for key, (kind, value, mode) in before.items():
        p = Path(key)
        if kind == "absent":
            assert not p.exists(), f"{adapter.name}: revert created {p}"
            continue
        assert p.exists(), f"{adapter.name}: revert removed {p}"
        if kind == "link":
            assert p.is_symlink(), f"{adapter.name}: {p} was a symlink, revert wrote a file"
            assert str(p.readlink()) == value, f"{adapter.name}: {p} no longer points at {value}"
        else:
            assert p.read_bytes() == value, f"{adapter.name}: {p} was not restored byte-for-byte"
            assert p.stat().st_mode & 0o777 == mode, f"{adapter.name}: {p} lost its mode"


def _refuses_empty_snapshot(adapter: Adapter) -> bool:
    """True if this adapter refuses to revert on an empty snapshot."""
    try:
        adapter.revert({})
    except Exception:
        return True
    return False


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _mutation_commands(cls) -> bool:
    """Does this adapter run a command as its mutation (rather than only reading)?"""
    tree = ast.parse(Path(inspect.getsourcefile(cls)).read_text())
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("run_checked", "run_optional")
        for node in ast.walk(tree)
    )


# ── 1. Shape of the contract ───────────────────────────────────────


def test_adapter_has_a_name(adapter: Adapter) -> None:
    assert adapter.name, f"{type(adapter).__name__} must define `name`"


def test_stateful_adapters_declare_why_they_are_not(adapter: Adapter) -> None:
    """An adapter that cannot restore must say so in the contract, not in a comment."""
    if adapter.stateful:
        return
    doc = (inspect.getmodule(type(adapter)).__doc__ or "").lower()
    assert "restore" in doc or "cannot" in doc, (
        f"{adapter.name}: stateful=False must be explained in the module docstring"
    )


def test_every_adapter_is_registered_in_the_orchestrator(adapter: Adapter) -> None:
    """Writing an adapter and forgetting to append it is the classic miss."""
    registered = {a.name for a in NightpanelOrchestrator().adapters}
    assert adapter.name in registered, f"{adapter.name} is not in the orchestrator's adapter list"


def test_declared_paths_live_inside_the_injected_paths(adapter: Adapter, env: Env) -> None:
    for p in adapter.restorable_paths():
        assert str(p).startswith(str(env.root)), (
            f"{adapter.name}: {p} is outside the injected Paths — an import-time "
            "Path.home() or a hardcoded absolute path"
        )


# ── 2. Capture is observable ───────────────────────────────────────


def test_stateful_adapter_captures_something(adapter: Adapter) -> None:
    if not adapter.stateful:
        return
    snap = adapter.snapshot()
    assert snap, f"{adapter.name}: stateful adapter returned an empty snapshot"
    if not any(bool(v) for v in snap.values()):
        raise AssertionError(
            f"{adapter.name}: snapshot captured nothing observable from seeded state — "
            "either capture something or declare stateful=False with a reason"
        )


def test_snapshot_survives_the_state_file_json_round_trip(adapter: Adapter) -> None:
    """The orchestrator persists snapshots as JSON; a capture that cannot survive
    that round trip is not a capture."""
    snap = adapter.snapshot()
    revived = json.loads(json.dumps(snap))
    assert revived == snap, f"{adapter.name}: snapshot is not JSON-safe"


# ── 3. The round trip ──────────────────────────────────────────────


def test_apply_changes_what_it_declares(adapter: Adapter) -> None:
    """An adapter that declares a restorable path must actually write it."""
    paths = adapter.restorable_paths()
    if not paths:
        return
    before = _snapshot_state(adapter)
    adapter.apply(BATTERY_PALETTE)
    if all(_snapshot_state(adapter)[str(p)] == before[str(p)] for p in paths):
        raise AssertionError(
            f"{adapter.name}: apply() changed nothing it declared restorable"
        )


def test_round_trip_restores_declared_paths_byte_for_byte(adapter: Adapter) -> None:
    before = _snapshot_state(adapter)
    snap = adapter.snapshot()
    adapter.apply(BATTERY_PALETTE)
    adapter.revert(snap)
    _assert_unchanged(adapter, before)


def test_double_apply_then_single_revert_restores(adapter: Adapter) -> None:
    """The double-apply corruption class: the second apply must not capture the
    applied palette as the baseline."""
    before = _snapshot_state(adapter)
    snap = adapter.snapshot()
    adapter.apply(BATTERY_PALETTE)
    adapter.apply(BATTERY_PALETTE)
    adapter.revert(snap)
    _assert_unchanged(adapter, before)


def test_a_non_utf8_file_comes_back_byte_for_byte(adapter: Adapter, env: Env) -> None:
    """A file that is not UTF-8 must come back exactly as it was, or the adapter
    must refuse — it may not write base64 into the user's file.

    `capture()` stores non-UTF8 content as base64 with a `binary` flag. An adapter
    that then parses or writes `captured["content"]` as text puts the base64 in the
    user's file, and an adapter that calls `read_text()` on it crashes with a
    `ValueError` that escapes `except OSError` and aborts the toggle for every other
    tool. Refusing with `AdapterError` is the honest outcome; both of those are not.
    """
    paths = adapter.restorable_paths()
    if not paths:
        return
    binary = b"\xff\xfe\x00\x01 not text \x80\x81\n"
    target = paths[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(binary)

    try:
        snap = adapter.snapshot()
        adapter.apply(BATTERY_PALETTE)
        adapter.revert(snap)
    except (AdapterError, AdapterNotLive):
        return  # refusing to guess is an honest outcome

    assert target.read_bytes() == binary, (
        f"{adapter.name}: a non-UTF8 file was not restored byte-for-byte — "
        "revert wrote the base64 capture as content instead of decoding it"
    )


def test_revert_refuses_an_empty_snapshot(adapter: Adapter) -> None:
    """Reverting on a guess is the worst outcome this tool can produce."""
    if not adapter.stateful:
        return
    assert _refuses_empty_snapshot(adapter), (
        f"{adapter.name}: stateful adapter reverted on an empty snapshot"
    )


# ── 4. Failure is not swallowed ────────────────────────────────────


def test_write_failure_raises_instead_of_logging(adapter: Adapter, env: Env) -> None:
    unwritable = env.root / "unwritable"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    fragile = type(adapter)(paths=Paths(home=unwritable), runner=env.runner)
    try:
        fragile.apply(BATTERY_PALETTE)
    except Exception:
        return
    raise AssertionError(f"{adapter.name}: a failed write was recorded as a success")


def test_command_failure_is_not_swallowed(adapter: Adapter, env: Env) -> None:
    """With a tool that is running and a command that fails, apply() must raise —
    `AdapterError`, or `AdapterNotLive` if the adapter says no session is normal.

    Reporting success is the lie this rule exists to prevent: the orchestrator's
    accounting and ACTIVE_FILE state machine are built on top of it. Adapters whose
    commands are pure reads are exempt — their result is used, so nothing is
    swallowed.
    """
    if not _mutation_commands(type(adapter)):
        return
    failing = RecordingRunner(fail_all=True)
    fragile = type(adapter)(paths=env.paths, runner=failing)
    try:
        fragile.apply(BATTERY_PALETTE)
    except Exception:
        return
    raise AssertionError(
        f"{adapter.name}: a mutation command failed during apply() and the adapter "
        "reported success. Use run_checked(), or run_optional(cmd, why)."
    )


# ── 5. capture() records what it promises ─────────────────────────


def test_capture_records_existence_mode_and_symlinks(adapter: Adapter, env: Env) -> None:
    target = env.root / "target"
    target.write_text("original\n")
    target.chmod(0o640)
    link = env.root / "link"
    link.symlink_to(target)

    captured_file = adapter.capture(target)
    captured_link = adapter.capture(link)

    assert captured_file["existed"] is True
    assert captured_file["mode"] == 0o640, "mode is not recorded — an executable file comes back non-executable"
    assert captured_link["link_to"] == str(target), "a symlink must be recorded as a link, not as content"

    missing = env.root / "missing"
    assert adapter.capture(missing)["existed"] is False

    # Restore must put the link back as a link.
    adapter.restore(link, captured_link)
    assert link.is_symlink() and str(link.readlink()) == str(target)
    adapter.restore(target, captured_file)
    assert target.read_text() == "original\n" and target.stat().st_mode & 0o777 == 0o640


def test_restore_refuses_an_incomplete_capture(adapter: Adapter, env: Env) -> None:
    p = env.root / "some-file"
    p.write_text("user content\n")
    with pytest.raises(Exception, match=f"{adapter.name}"):
        adapter.restore(p, {})
    assert p.read_text() == "user content\n", "refusing must not damage the file it refused to touch"


# ── 6. Static shape ────────────────────────────────────────────────

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def test_adapter_module_follows_the_rules(adapter: Adapter) -> None:
    """The rules that keep this class of bug out, checked on the source itself."""
    src = Path(inspect.getsourcefile(type(adapter))).read_text()
    tree = ast.parse(src)

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    assert "subprocess" not in imports, f"{adapter.name}: must shell out through the injected Runner"
    assert "logging" not in imports, f"{adapter.name}: base.py owns logging; adapters raise AdapterError"

    assert "Path.home()" not in src, (
        f"{adapter.name}: import-time Path.home() — resolve through the injected Paths"
    )
    # Docstrings are excluded: a comment explaining a past hardcoded hex is not a
    # hardcoded hex. Only strings the code can act on count.
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    } - {ast.get_docstring(tree)}

    hex_hits = sorted(s for s in literals if _HEX.search(s))
    assert not hex_hits, f"{adapter.name}: hardcoded hex; the Palette is the source of truth"

    # A string that *looks like a filesystem location*, not a comment marker that
    # happens to start with a slash.
    absolute = sorted(
        s
        for s in literals
        if re.fullmatch(r"(/[A-Za-z0-9._-]+)+", s) and s not in {"/proc", "/sys"}
    )
    assert not absolute, (
        f"{adapter.name}: absolute path literal {absolute} bypasses the injected Paths"
    )

    # Rule #1, checked on the source: a command result thrown away anywhere in the
    # adapter is a failure recorded as a success. A mutation must be either
    # run_checked (must land) or run_optional with a stated reason (the tool is not
    # running). Helpers count: a helper that discards is the same lie as apply()
    # discarding.
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "run"
    ]
    assert not offenders, (
        f"{adapter.name}: self.run() result thrown away at line(s) {offenders}. "
        "Use run_checked(), or run_optional(cmd, why) if no session is normal."
    )

    # Rule #3 on the source: a file is read through base's helpers, which raise
    # AdapterError, or through capture(), which records what must be put back.
    # Path.read_text() raises UnicodeDecodeError — a ValueError that escapes
    # except OSError and aborts the toggle for every other tool.
    raw_reads = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self")
    ]
    assert not raw_reads, (
        f"{adapter.name}: Path.read_text() at line(s) {raw_reads}; "
        "use self.read_text() or capture() so an unreadable file is an AdapterError, not a crash."
    )

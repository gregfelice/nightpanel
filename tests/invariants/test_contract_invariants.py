# SPDX-License-Identifier: GPL-3.0-or-later
"""The contract battery.

Every test here is parametrized over every Adapter subclass defined by a module
in ``nightpanel/adapters/``, so a new adapter inherits the whole battery without
anyone writing a test for it. That is the point: the rules in
``adapters/base.py`` are only real if a violation fails here.

Discovery walks the directory rather than the package namespace, because the
namespace only shows what was imported. An adapter module that is never imported
is invisible to every check in this file — including the registration check —
and forgetting the import is the classic miss. So the battery discovers from the
files and then requires the module to be exported.

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
import os
import re
from pathlib import Path

import pytest

from nightpanel.adapters import Adapter
from nightpanel.adapters.base import AdapterError, AdapterNotLive
from nightpanel.palette import Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner
from nightpanel.services.nightpanel_orchestrator import NightpanelOrchestrator

from .conftest import LIVE_ANSWERS, USER_CONTENT, Env, StatefulRunner, all_adapters

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


def test_every_discovered_adapter_is_exported_by_the_package(adapter: Adapter) -> None:
    """The other half of registration.

    Discovery reads the directory, so an adapter module that is never imported
    still reaches the battery — and then has to be exported, or the miss the
    battery is meant to catch is the miss it cannot see.
    """
    import nightpanel.adapters as pkg

    cls = type(adapter)
    assert getattr(pkg, cls.__name__, None) is cls, (
        f"{adapter.name}: defined in {inspect.getmodule(cls).__name__.split('.')[-1]}.py but not "
        "exported by nightpanel.adapters — the battery discovers adapters through the package, "
        "so an unimported module is invisible to every check here"
    )


def test_declared_paths_live_inside_the_injected_paths(adapter: Adapter, env: Env) -> None:
    """Both kinds of declared path, not just the restorable ones.

    Checking only restorable_paths() leaves own_paths() unpoliced: an adapter can
    declare a hardcoded absolute path as its own and pass.
    """
    for p in adapter.restorable_paths() + adapter.own_paths():
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
        raise AssertionError(f"{adapter.name}: apply() changed nothing it declared restorable")


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


def test_write_does_not_write_through_a_symlink(adapter: Adapter, env: Env) -> None:
    """A path that is a symlink is replaced, not written through.

    `capture()` records a link as a link with no content, so a write through it mutates
    a file the capture cannot put back. That is how claude_code's wrapper landed on the
    real binary under `versions/`: the promise that revert restores what capture
    recorded only holds if the write stays at the recorded location.
    """
    target = env.root / "the-users-real-file"
    target.write_text("the user's real file\n")
    link = env.root / "link"
    link.symlink_to(target)

    adapter.write(link, "nightpanel applied\n")

    assert target.read_text() == "the user's real file\n", (
        f"{adapter.name}: write() followed the link and mutated the file behind it"
    )
    assert not link.is_symlink(), f"{adapter.name}: write() left the link in place"


def test_read_text_does_not_turn_absence_into_a_failure(adapter: Adapter, env: Env) -> None:
    """A missing file is empty state, not a read failure.

    Every adapter treats absence as "nothing there yet" and creates the file. A
    `FileNotFoundError` converted into `AdapterError` makes a fresh profile raise, and a
    raw `FileNotFoundError` escaping an adapter aborts the toggle for every other tool.
    """
    missing = env.root / "not-there-yet"
    try:
        adapter.read_text(missing)
    except FileNotFoundError:
        return
    except AdapterError as e:
        raise AssertionError(
            f"{adapter.name}: read_text() reported absence as a failure: {e}"
        ) from None
    raise AssertionError(f"{adapter.name}: read_text() returned content for a missing file")


def test_a_fresh_profile_is_created_or_refused(adapter: Adapter, tmp_path: Path) -> None:
    """The user with no file yet is a normal case, not a crash.

    Either apply() creates the file or it refuses with `AdapterError` / `AdapterNotLive`.
    A raw exception escaping the adapter is the failure shape this rule exists to catch:
    it aborts the toggle for every other tool. Run on an unseeded fixture, because the
    seeded one is the "user already had a file" case.
    """
    fresh = type(adapter)(
        paths=Paths(home=tmp_path / "fresh"), runner=adapter.runner, palette=adapter.palette
    )
    paths = fresh.restorable_paths()
    if not paths:
        return  # acts on a live session, or has no user file to create
    try:
        fresh.apply(BATTERY_PALETTE)
    except (AdapterError, AdapterNotLive):
        return
    assert any(p.exists() for p in paths), (
        f"{fresh.name}: apply() on a fresh profile wrote nothing and did not refuse"
    )


def test_every_user_file_an_adapter_writes_is_declared_restorable(
    adapter: Adapter, env: Env
) -> None:
    """Rule #3 checked on the write itself.

    Every file an adapter writes must be either a user file it commits to put back
    (`restorable_paths()`) or a file it declares as its own (`own_paths()`). A file that
    is neither is a user file written without a capture that could put it back — the
    claim has to be in the contract, not in a comment.
    """
    written: list[Path] = []
    original = adapter.write

    def recording(path: Path, content: str) -> None:
        written.append(path)
        original(path, content)

    adapter.write = recording
    try:
        adapter.apply(BATTERY_PALETTE)
    except (AdapterError, AdapterNotLive):
        pass  # refusing is an honest outcome

    declared = {str(p) for p in adapter.restorable_paths()} | {str(p) for p in adapter.own_paths()}
    undeclared = [str(p) for p in written if str(p) not in declared]
    assert not undeclared, (
        f"{adapter.name}: wrote {undeclared} without declaring them restorable or its own"
    )

    # Rule #6 on the write itself, not on the declaration. A declared path can
    # still resolve outside the fixture — through expanduser(), os.environ["HOME"],
    # or a symlink that leaves the root — and the declaration test cannot see that.
    outside = [str(p) for p in written if not str(p).startswith(str(env.root))]
    assert not outside, f"{adapter.name}: wrote {outside} outside the injected Paths root"


def test_a_declared_own_path_that_already_exists_is_captured_or_refused(
    adapter: Adapter, env: Env
) -> None:
    """A declaration of ownership does not make a file that is already there ours.

    `own_paths()` is the escape hatch in the contract: a path declared as ours has
    nothing to put back. That is only true for a file the adapter created. A file
    standing at a declared own path before apply is user state whatever the
    declaration claims, so the adapter must have captured it or refused — otherwise
    declaring a path as ours is a way to destroy a user file with no way back, which
    is the same damage rule #4 refuses, reached through the declaration instead of
    through revert.

    The rule is checked on the round trip, not on the source: seed user content at
    every declared own path, apply, revert, and the content has to be back. An
    adapter that cannot capture it has to refuse, and refusing is an honest outcome.
    """
    own = adapter.own_paths()
    if not own:
        return

    for p in own:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(USER_CONTENT)
    before = {str(p): p.read_bytes() for p in own}

    try:
        snap = adapter.snapshot()
        adapter.apply(BATTERY_PALETTE)
        adapter.revert(snap)
    except (AdapterError, AdapterNotLive):
        return  # refusing is an honest outcome

    for key, content in before.items():
        p = Path(key)
        # An adapter that deletes a file it never captured is doing the same damage as
        # one that overwrites it, and the failure has to read as a rule violation rather
        # than as a FileNotFoundError escaping the test.
        assert p.exists(), (
            f"{adapter.name}: {p} was declared as its own, but a file already there is "
            "user state — revert() removed a file snapshot() had not captured"
        )
        assert p.read_bytes() == content, (
            f"{adapter.name}: {p} was declared as its own, but a file already there is "
            "user state — snapshot() did not capture it and revert() did not put it back"
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
    if os.geteuid() == 0:
        # Root is not blocked by mode bits, so the write would succeed and the test
        # would report a pass for a rule it never checked.
        pytest.skip("chmod 0o500 does not block writes for root")
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
    assert captured_file["mode"] == 0o640, (
        "mode is not recorded — an executable file comes back non-executable"
    )
    assert captured_link["link_to"] == str(target), (
        "a symlink must be recorded as a link, not as content"
    )

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
    assert p.read_text() == "user content\n", (
        "refusing must not damage the file it refused to touch"
    )


# ── 6. Static shape ────────────────────────────────────────────────

_HEX = re.compile(r"#[0-9a-fA-F]{6}")

#: Writes that bypass base.write()/restore(). base.write() is where a symlink is
#: replaced rather than followed and where a failed write becomes an AdapterError
#: instead of a logged warning; bypassing it escapes both, and it is invisible to
#: the runtime check that records what an adapter wrote.
_RAW_WRITE_ATTRS = ("write_text", "write_bytes", "symlink", "copy", "copy2", "copytree")


def _static_violations(src: str) -> dict[str, list]:
    """The rules that can be checked on the source, without executing it.

    Every list empty means the source follows the contract. The battery asserts
    emptiness for the real adapters; the self-check asserts non-emptiness for a
    deliberately bad one, which is what makes each of these a rule rather than a
    preference.
    """
    tree = ast.parse(src)

    def not_on_self(node: ast.Call) -> bool:
        return not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self")

    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    # Docstrings are excluded: a comment explaining a past hardcoded hex is not a
    # hardcoded hex. Only strings the code can act on count.
    literals = {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    } - {ast.get_docstring(tree)}

    calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]

    return {
        "subprocess": ["subprocess"] if "subprocess" in imports else [],
        "logging": ["logging"] if "logging" in imports else [],
        "home": ["Path.home()"] if "Path.home()" in src else [],
        "hex": sorted(s for s in literals if _HEX.search(s)),
        # A string that *looks like a filesystem location*, not a comment marker
        # that happens to start with a slash.
        "absolute": sorted(
            s
            for s in literals
            if re.fullmatch(r"(/[A-Za-z0-9._-]+)+", s) and s not in {"/proc", "/sys"}
        ),
        # Rule #1: a command result thrown away anywhere in the adapter is a
        # failure recorded as a success. Helpers count — a helper that discards is
        # the same lie as apply() discarding.
        "discarded_run": [
            n.lineno for n in calls if isinstance(parents.get(n), ast.Expr) and n.func.attr == "run"
        ],
        # Rule #3 on the source: read through base's helpers, which raise
        # AdapterError, or through capture(), which records what must be put back.
        # Path.read_text() raises UnicodeDecodeError — a ValueError that escapes
        # except OSError and aborts the toggle for every other tool.
        "raw_reads": [n.lineno for n in calls if n.func.attr == "read_text" and not_on_self(n)],
        "raw_writes": [
            n.lineno for n in calls if n.func.attr in _RAW_WRITE_ATTRS and not_on_self(n)
        ],
    }


def test_adapter_module_follows_the_rules(adapter: Adapter) -> None:
    """The rules that keep this class of bug out, checked on the source itself."""
    found = _static_violations(Path(inspect.getsourcefile(type(adapter))).read_text())
    for rule, offenders in found.items():
        assert not offenders, f"{adapter.name}: {rule} violation {offenders}"


# ── 7. Verify agrees with what apply did ───────────────────────────


def test_verify_agrees_with_what_apply_did(adapter: Adapter, env: Env) -> None:
    """apply → verify("on") → revert → verify("off"), for every adapter.

    This is the happy path the BACKLOG claimed the battery ran, and it could not be
    written against a canned runner: the adapter wrote something and the fake never
    looked at it, so verify("on") was only ever true for the adapters whose state is
    a file. With the modelled tools it is real for the live-session adapters too.

    The fixture has already seeded this adapter's user state, so the only thing
    changed here is the runner — and the palette, because verify() compares against
    the palette it was handed and the fixture hands it the default one.
    """
    adapter.runner = StatefulRunner(prefix=LIVE_ANSWERS.get(adapter.name))
    adapter.palette = BATTERY_PALETTE

    snap = adapter.snapshot()
    adapter.apply(BATTERY_PALETTE)
    assert adapter.verify("on"), f"{adapter.name}: verify() does not agree with what apply() did"

    adapter.revert(snap)
    assert adapter.verify("off"), f"{adapter.name}: verify() does not agree with what revert() did"


def _verify_reads_palette(cls: type) -> bool:
    """Whether verify() looks at the palette, or at something that is the same for
    every palette (a sentinel, a theme name, an import line)."""
    src = inspect.getsource(cls)
    match = re.search(r"def verify\(.*?(?=\n    def |\Z)", src, re.S)
    return bool(match) and "self.palette" in match.group(0)


def test_verify_tracks_the_palette(adapter: Adapter, env: Env) -> None:
    """The half of rule #7 that can actually bite — and it bites on one adapter.

    Only a verify() that reads the palette can tell that a *different* palette was
    applied. The other nine verify on a sentinel, a theme name, or an import line, so
    they answer "on" for every palette. That is a legitimate answer for them, but it
    means this rule cannot check them, and a rule that checks nothing is a preference.

    So the rule is scoped to the adapters that can answer it, and the skip says which.
    Today that is tmux alone. The self-check proves the rule is not vacuous: an
    adapter that compares against the default palette instance instead of the one it
    was handed passes the happy path and fails here.
    """
    if not _verify_reads_palette(type(adapter)):
        pytest.skip(f"{adapter.name}: verify() is not derived from the palette")

    other = Palette(name="other", bg="#202020", bg_header="#202020", fg="#406040")
    adapter.runner = StatefulRunner(prefix=LIVE_ANSWERS.get(adapter.name))
    adapter.palette = BATTERY_PALETTE

    adapter.apply(BATTERY_PALETTE)
    assert adapter.verify("on"), f"{adapter.name}: verify() does not agree with what apply() did"

    # Same live state, different palette. A verify that tracks the palette now says
    # no; one that compares against a fixed instance still says yes, and that is the
    # bug this rule exists to catch.
    adapter.palette = other
    assert not adapter.verify("on"), (
        f"{adapter.name}: verify() still says 'on' for a palette it was not applied with"
    )

# ADR-001 — The adapter contract is enforced by a battery, not by prose

Status: accepted, 2026-10-14. Repo-local; the estate ADRs (030, 048, 073) govern the
pipeline this runs inside.

## Context

Nightpanel mutates a dozen unrelated user configs — alacritty TOML, tmux options,
nvim lua, gtk.css, Firefox userChrome.css, a native-messaging manifest, wrapper
scripts. Every bug found so far is one of four shapes, and none of them is a bug in a
particular adapter:

1. **Silent failure** — the adapter logs a warning and returns; the orchestrator records
   success; `nightpanel-active` claims the world is on while nothing changed.
2. **Guess-based revert** — `snapshot()` returns `{}` and `revert()` restores a hardcoded
   default instead of what it captured.
3. **Import-time paths** — module-level `Path.home()` constants, so tests are hermetic only
   by monkeypatching globals and any XDG layout is wrong by construction.
4. **Verify that tests one palette** — `bg=#000000` hardcoded instead of derived from the
   palette instance.

Documenting these rules in `adapters/base.py` did not stop them. A rule that only a
reviewer can check is a rule that will be broken by the next adapter, and there are more
adapters coming.

## Decision

The contract is a set of invariants checked against **every** `Adapter` subclass
discovered by walking `nightpanel.adapters`, in `tests/invariants/`. A new adapter is
picked up automatically; nobody writes a test for it.

The rules, as they are now checkable:

| Rule | What the battery refuses |
| --- | --- |
| A failure is an outcome | A `self.run(...)` whose result is thrown away anywhere in the adapter module. A mutation must be `run_checked()` (must land) or `run_optional(cmd, why)` (the tool is not running), and both must reach the caller as an exception. |
| Revert is restoration | A `stateful` adapter that accepts an empty `snapshot()`. It must refuse, or declare `stateful = False` with the reason in the module docstring. |
| Paths are injected | Module-level `Path.home()` or an absolute path literal in an adapter. Everything the adapter writes comes from the injected `Paths`. |
| Palette is the source of truth | A hex literal in an adapter. |
| The round trip is byte-for-byte | A declared path whose content, existence, mode, or symlink-ness changes across apply → revert. |
| An own path is ours only when we created it | A file already standing at a declared `own_paths()` entry — overwritten without a capture that could put it back, or removed by a revert that never captured it. Declaring a path as ours is a claim about what the adapter created, not about what is standing at the path. |
| The battery is not a ceremony | A deliberately bad adapter (`tests/invariants/test_battery_self_check.py`) must fail the same checks that the real adapters pass. |

Two consequences that were not obvious when the rules were written:

- **"No live session" is not a failure, but it is still an outcome.** `run_optional(cmd, why)`
  requires the reason at the call site, because a swallowed failure and an optional failure
  look identical in the code. It raises `AdapterNotLive`, which the orchestrator records as a
  distinct outcome — never as success. A class-level `live_optional` flag was tried first and
  rejected: it exempts the whole adapter, including the mutations that must land.
- **`RecordingRunner` distinguishes "answered with nothing" from "not running"**
  (`no_session=True`). An adapter that treats those two states as the same cannot pass a test
  that separates them, which is the point of the option.

## Consequences

- Adapters raise. `AdapterError` and `AdapterNotLive` propagate to the orchestrator, which
  reports per-adapter outcomes and gates `nightpanel-active` on every stateful adapter both
  capturing and applying. A half-collected snapshot is not a baseline, so the orchestrator
  refuses to engage for an adapter that could not capture.
- `./bin/test` runs `tests/invariants/` alongside `tests/unit/`, and CI calls the same script
  (ADR-030). The battery is the gate, so it cannot be skipped by not writing a test.
- Per-adapter unit tests stay for behaviour the battery cannot see — TOML round trips,
  `profiles.ini` priority, marker stripping, the wrapper's exec semantics. The battery owns
  the contract-shaped ones. Tests that asserted the old swallow-and-log behaviour were
  rewritten to assert the raise; they were not repaired.
- Cost: adapters that previously tolerated a missing tool now propagate `AdapterNotLive`, so
  the UI and the journal must render an outcome that is neither success nor failure. That is
  tracked in `docs/BACKLOG.md`.

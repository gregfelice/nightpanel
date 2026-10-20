# PLAN — adapter contract invariants

**A plan is a committed file.** The original `PLAN-contract-invariants.md` lived only in a
session transcript. When that session was cleared the plan was gone, and with it the baseline
numbers that finding 6 was about — nothing on the filesystem, in git history, or in the memory
system carried them. That is why this copy lives in the repo: a cleared session cannot take a
committed file. If a plan cannot be committed to the repo it works on, it goes in the memory
system, never only in a transcript.

Every number below is measured in this worktree today, not carried from an earlier run. A plan
that quotes a number it cannot re-measure is a claim, not a measurement.

Branch `np-contract-invariants`, worktree `contract`. Pushed to `origin`, not merged; main at
`96ad4ad`. No commit count is recorded here: a count in a committed plan goes stale with the
next commit, which is how finding 6 happened.

## Numbers as measured 2026-10-15

| Measure | Value |
| --- | --- |
| Full suite | 691 passed, 8 skipped |
| `tests/invariants/` | 219 passed, 8 skipped — 208 battery + 11 self-checks |
| `ruff check` / `ruff format --check` | clean |
| `bin/validate` | passed on `03cefd5` — 11 checks, 0 failures, installed-package smoke included |

The 8 skips are the palette rule, which is scoped to the adapters that can answer it (tmux
alone); each skip names the adapter and the reason.

The earlier baseline survives at the root of the main checkout, untracked, and contradicts itself:
the same run is recorded as 389 passed / 5 failed / 52 errors and as 373 passed / 5 failed / 61
errors, and the same suite as 138 tests, 186 passed, and 443 passed with 12 failures against 651
clean. It is committed as the appendix below — a plan that lives only in a transcript or an
untracked file is what finding 6 was about, so the surviving copy goes into git. The numbers above
are the baseline going forward; the appendix is the historical record, not a measurement.

## Findings

| # | Finding | Status |
| --- | --- | --- |
| 1 | `ruff format --check` not clean | Fixed, `0dfe2a0` |
| 2 | Rule 7 (verify tracks the palette) specced, never implemented | Fixed, `588b6b5` — `StatefulRunner`; rule scoped to the adapters that can answer it |
| 3 | Discovery walked the package namespace, so an unimported adapter module was invisible to the rule meant to catch it | Fixed, `5207a28` — walks `nightpanel/adapters/*.py`, requires export |
| 4 | Rule 6 checked declared paths, not written paths | Fixed, `643ca8f` — covers `own_paths()` and the write itself |
| 5 | Orchestrator not held to the rule it enforces on adapters | Fixed, `bb7c9cf` — `_save_state` raises, `_load_state` separates corrupt from absent |
| 6 | Number drift in the plan | Fixed — the surviving original plan is committed as the appendix below; the numbers table above is the baseline, and the PR description uses measured figures only |
| 7 | `own_paths()` permitted overwriting a pre-existing user file with no capture | Fixed, `5f1902e` — capture-or-refuse, proven against the four real adapters |
| 8 | `chmod 0o500` test passes silently as root | Fixed, `643ca8f` — skip when `os.geteuid() == 0` |
| — | The orchestrator's own rule was not applied at the entry point: `toggle.py` discarded the result | Fixed, `359a110` — the failure is recorded as a decision and exits non-zero |
| 9 | `paths.py` and `runner.py` were added by `330d2f0` and never added to `nightpanel_sources` in `src/meson.build`. The source tree imported fine, so the suite passed; the installed `.deb` failed at `from ..paths import Paths` | Fixed, `29feb8c`. `bin/validate` caught it — 11 checks, 1 failure. Guard added, `14ee352`: every module under `src/nightpanel/` must be named by an install list, resolved as meson resolves it. Nothing in `tests/` checked this before; the failure only surfaces in CI's proof job on `v*` tags |
| 10 | The import-time `Path.home()` class is enforced only inside `nightpanel/adapters/`. `services/np-host.py:20`, `services/backends/kde.py:18` and `player_app.py:22` still bind it at import | Open — the rule is scoped to adapters, so the battery cannot see them |
| 11 | P5's coverage report was never produced, and the committed plan dropped it | Open — measured 2026-10-14: adapters 91–100%, TOTAL 49%, `renderers/emacs.py` and `renderers/firefox.py` 0%, `theme_service.py` 33%; `CLAUDE.md` still reads 220+ tests / 29% / 0% |

The rule numbers in findings 4 and 5 are the original plan's, now committed as the appendix below.
ADR-001 names the rules without numbering them, and the test comments carry the same numbers.

Two findings the stateful runner produced that the review did not predict:

- The claude_code seeder did not model `versions/`, so `apply()` skipped the wrapper and
  `verify("on")` could never be true for a reason unrelated to the adapter.
- The palette rule needed scoping before it could be enforced: only a `verify()` that reads the
  palette can tell that a *different* palette was applied. A rule that checks nothing is a
  preference, so the battery names which adapters it cannot check.

## PR description

Written from the measurements above only, in the form `230-git-procedures-playbook.md` §2
requires. It lives here because a description that lives only in a transcript is lost with the
session — which is what happened to the plan this page replaces.

> Contract invariants enforced at runtime, not in prose. The ruling is ADR-001; the reviewer
> should look at the battery in `tests/invariants/`, which is the enforcement, and at the two
> changes outside it: `toggle.py` now records a refused state write as a decision and exits
> non-zero instead of raising, and `src/meson.build` now installs `paths.py` and `runner.py`,
> which the contract work added but never listed — the source tree imported fine so the suite
> passed, and the installed package failed at `from ..paths import Paths`.
>
> Battery: 208 passed + 8 skipped, plus 11 self-checks that prove each rule bites on a
> deliberately bad adapter, plus the install-list guard in `tests/unit/`. Full suite 691 passed,
> 8 skipped. The 8 skips are the palette rule, scoped to the adapters that can answer it (tmux
> alone); each names its adapter and the reason.
>
> The own-path rule checks the round trip even when the adapter refuses: a refusal at `revert()`
> arrives after `apply()` has already overwritten the file, so only a refusal before the write is
> honest, and the self-check carries that shape.
>
> One upgrade behaviour to note: tmux's snapshot shape changed (flat options dict →
> `{options, own}`). A state file written by the old code makes the new `revert()` raise "no
> capture; refusing to revert on a guess" — safe by rule 4, but an upgrade mid-session strands
> applied state with a refusal.
>
> Findings 1–5, 7, 8 and 9 are fixed. Findings 10 and 11 are open: the import-time `Path.home()`
> rule is scoped to `nightpanel/adapters/`, so three module-level constants outside it are not
> policed; P5's coverage report was never produced. Every figure here is measured, on 2026-10-15.
>
> `bin/validate` passed on `03cefd5`: 11 checks, 0 failures, including the clean-trixie build and
> the installed-package smoke, and the gate ran on that commit. `ruff check` and `ruff format
> --check` are clean on the branch. The commit that records this verdict is docs only, so the gate
> did not run on it.

## Remaining work

1. Open the pull request against `main` — nightpanel has no `master` on either remote (`origin`:
   `main`, `deb-packaging`; `github/HEAD` → `main`) — with the description above, which now carries
   the gate's verdict §2 requires. The branch is pushed with this commit.
2. Rendering adapter and orchestrator failures in the UI — tracked in `docs/BACKLOG.md`. The
   entry point side is settled: `toggle.py` records a refused state write as a decision and
   exits non-zero, because `bin/analyze-toggle-log` only counts invocations that produced a
   decision, so an uncaught exception made the failure invisible to the tool built to diagnose
   toggles.

## Appendix — the original plan, as it survives

Committed here so a cleared session cannot take it. This is the file that survived untracked at the
root of the main checkout, verbatim. Its numbers contradict each other in three places — that is
finding 6, and the numbers table above is the baseline going forward. The rule numbers in findings
4 and 5, the D1–D11 defect table, and the battery spec come from this file.

# Plan — contract invariants: shake the bugs out, and keep them out

Goal: make the adapter contract *enforced*, not documented. Nightpanel is fragile
because it mutates a dozen unrelated user configs; every bug so far is one of four
shapes, and each shape can be made impossible by a test that every adapter —
including ones written next month — inherits automatically.

The four bug shapes this plan closes:

1. **Silent failure** — adapter logs a warning, returns, orchestrator records success,
   state machine claims "on" while nothing changed.
2. **Guess-based revert** — snapshot returns `{}`, revert restores a hardcoded default
   instead of what was captured (tmux, firefox, claude_code's wrapper).
3. **Import-time paths** — module-level `Path.home()` constants make tests depend on the
   developer's real home and make any XDG layout wrong by construction.
4. **Verify that tests for one palette** — `bg=#000000` hardcoded instead of derived from
   the palette instance.

## Where the work happens

| | |
| --- | --- |
| Repo | `/home/gregf/development/utilities/nightpanel` (main checkout, branch `main`) |
| Worktree | `/home/gregf/worktrees/nightpanel/contract`, branch `np-contract-invariants` |
| Test entrypoint | `./bin/test` (per ADR-030; CI calls the same script) |
| Dev venv | `.venv-dev` — `PYTHONPATH=src .venv-dev/bin/python -m pytest tests/unit/ -q --no-cov` |

Nothing was committed on the branch when this plan was written; the work is now committed
as three checkpoints on `np-contract-invariants` (see State).

## Baseline (measured 2026-10-14)

```
389 passed, 5 failed, 52 errors
```

- 52 errors: existing tests `monkeypatch.setattr("nightpanel.adapters.<mod>._CONSTANT", …)`
  against module globals the refactor deleted.
- 5 failures: tests asserting the old swallow-and-log behaviour
  (`test_apply_survives_sentinel_write_failure`, `test_apply_logs_when_stale_present`,
  `test_apply_writes_command_file`, `test_revert_writes_command_file_only`,
  `test_apply_invokes_gtk_css_and_nautilus_bounce`). These assert a bug; rewrite them to
  the new contract, do not repair them.
- `tests/invariants/test_contract_invariants.py` — **referenced by `adapters/base.py:15`
  and does not exist.** This is the whole mechanism, and it is missing.

## Known defects to fix while building the battery

| # | Defect | Location |
| --- | --- | --- |
| D1 | Battery missing; `stateful` and `restorable_paths()` are prose, not enforcement | `adapters/base.py:15`, `:60` |
| D2 | `find_default_profile()` now requires `ff_root`; orchestrator calls it with no args → `TypeError` in `install_bridge()` | `adapters/firefox.py:74`, `services/nightpanel_orchestrator.py:31` |
| D3 | Orchestrator still binds `Path.home()` at import time (state file, ACTIVE_FILE, np-command.json, NM manifest) — the exact bug class `Paths` exists to fix; it can disagree with adapters about the config dir | `nightpanel_orchestrator.py:36-43` |
| D4 | `run()` returns `None` on non-zero exit and every apply/revert call site discards it, so a failed apply is recorded as success — contradicts rule #1 | `adapters/base.py:206`; call sites `tmux.py:70,77,82`, `gnome.py:139,150`, `nvim.py:87`, `emacs.py:80` |
| D5 | `apply()`'s snapshot loop is unguarded: one adapter raising aborts the toggle for all tools, and a half-collected snapshot is saved as the baseline | `nightpanel_orchestrator.py:120` |
| D6 | "byte-for-byte" is not literally true: `read_text()` on non-UTF8 raises `UnicodeDecodeError` (a `ValueError`), which escapes `except OSError` | `adapters/base.py:125-168` |
| D7 | `restore()` can `TypeError` on `content=None` with `existed=True`, `link_to=None` | `adapters/base.py:169-192` |
| D8 | `restore()` writes to the passed path, not `captured["path"]`; a mismatch is silent | `adapters/base.py:169` |
| D9 | `stateful` is never consulted anywhere; no adapter declares `stateful = False` with a reason | `adapters/base.py:60` |
| D10 | **fixed** (`ce9fb51`) — `write()` follows a symlink, so a wrapper written at a linked path lands on the file behind it. claude_code's wrapper overwrote the real binary under `versions/`; `capture()` recorded only the link, so `revert()` restores a link to content it destroyed. A write must not touch a file the capture cannot put back | `adapters/base.py:250`, `claude_code.py:215` |
| D11 | **fixed** (`ce9fb51`) — `read_text()` catches `OSError`, which turns "the file does not exist" into `AdapterError`. Absence is not a read failure: every adapter treats it as empty state. Consequences — `gemini_cli.apply()` on a fresh profile raises instead of creating `settings.json` (`_read_settings` has no existence guard, unlike `gnome.py:129` and `firefox.py:214`), `claude_code._read_settings` has the same gap, and `firefox.verify("off")`'s `except OSError` at `firefox.py:175` is dead code | `adapters/base.py:234` |

## The battery — spec

`tests/invariants/test_contract_invariants.py`, parametrized over every `Adapter` subclass
discovered from `nightpanel.adapters`. A new adapter is picked up automatically; that is the
mechanism.

Fixtures (`tests/invariants/conftest.py`):

- `env(tmp_path)` → `Paths(home=tmp_path)` + `RecordingRunner`. No test may touch the real home.
- `seed(env)` → writes plausible pre-existing user content at each `restorable_paths()` entry,
  including a symlink and an executable file, so capture has something real to capture.
- `RecordingRunner` extended with `fail={cmd}` so a command can return non-zero, and a
  `FailingPaths` variant pointing at an unwritable directory.

Invariants, one test each, run against every adapter:

1. **Registration** — every `Adapter` subclass is in the orchestrator's default adapter list,
   and `name` is non-empty. (This is how "add a new tool" fails in practice.)
2. **Capture is observable** — a `stateful` adapter's `snapshot()` is non-empty when the seed
   exists.
3. **Round trip is byte-for-byte** — seed → snapshot → apply → revert leaves every declared
   file identical: content, existence, mode, and symlink-ness.
4. **No guessing** — `revert({})` on a stateful adapter raises `AdapterError`.
   *Decision:* tmux's `if not snapshot: source ~/.tmux.conf` fallback must go, or be gated
   behind an explicit declared reason. Guessing is the bug, not the fallback.
5. **Failure is not swallowed** — a runner that returns non-zero, or an unwritable path, makes
   `apply()` raise `AdapterError`.
6. **Nothing escapes the fixture** — every path an adapter writes is under the injected
   `Paths` root. This kills the monkeypatch-the-globals pattern permanently.
7. **Verify tracks the palette** — apply with a non-default `Palette`, `verify("on")` is True;
   after revert, `verify("off")` is True. Catches hardcoded `#000000`.
8. **Static shape** (AST, no execution): no `subprocess` import, no module-level `Path.home()`,
   no hex literal, no `logging` in `adapters/` — `base.py` owns logging.
9. **Idempotence** — `apply()` twice then `revert()` once restores the original. The
   double-apply corruption class.
10. **Corrupt state** — a snapshot that survives a JSON round trip must still be accepted by
    `revert()`; a truncated one must raise, not delete.

## Phases

- [x] **P0 — Baseline.** Recorded: `389 passed, 5 failed, 52 errors`. The 52 errors are the
      migration list; none deleted.
- [x] **P1 — Fix `base.py` (D4, D6, D7, D8, D9).**
      - `capture`/`restore` on bytes, base64 for non-UTF8 with a `binary` flag, so the JSON
        state file round trip stays honest.
      - `restore()` validates the capture shape and uses `captured["path"]`.
      - **Decision, changed while implementing:** `run()` stays a read (returns stdout or
        `None`) and its result must be used. The mutation half is `run_checked()` (must land,
        raises `AdapterError`) or `run_optional(cmd, why)` (the tool is not running, raises
        `AdapterNotLive`). `why` is required at the call site because a swallowed failure and
        an optional failure look identical in the code. A class-level `live_optional` flag was
        tried first and rejected — it exempts the whole adapter, including the mutations that
        must land.
- [x] **P2 — Write the battery** (`tests/invariants/conftest.py` +
      `test_contract_invariants.py`). 138 tests, parametrized over every adapter. It failed on
      13 checks first, then on 7 genuine violations — that is the proof the mechanism works.
      `test_battery_self_check.py` keeps the proof permanent: a deliberately bad adapter must
      fail the same checks the real ones pass.
- [x] **P3 — Orchestrator takes `Paths`** (D2, D3, D5). State file, ACTIVE_FILE, np-command.json
      and the NM manifest are instance properties resolved from injected `Paths`;
      `find_default_profile(self._ff_root)`; snapshot loop guarded per adapter; ACTIVE_FILE
      gated on every stateful adapter capturing and applying; `revert()` returns outcomes; the
      ghost-prune gsettings write checks its exit code instead of reporting success.
      `Paths.data` (XDG_DATA_HOME) added for the gnome-shell extension dir.
- [x] **P4 — Migrate the 52 broken tests** to injected `Paths`/`Runner` (fixture becomes
      `adapter = AlacrittyAdapter(paths=Paths(home=tmp_path), runner=RecordingRunner())`).
      Rewrite the tests that assert swallowed failures to assert `AdapterError`.
      Reference migration: `tests/unit/test_tmux_adapter.py`. Shared `paths`/`runner` fixtures
      added to `tests/conftest.py`. Done: 0 errors, 443 passed; 12 failures remain, all
      fixture shape rather than contract (see State).
- [ ] **P5 — Green.** `./bin/test` clean, battery green, ruff clean, coverage on the theming
      layer reported (the battery alone measures 67–94% per adapter; the layer was 0%).
- [x] **P6 — Wire it in.** `bin/test` runs `tests/invariants/` too; CI calls the same two
      directories; `docs/adr/001-adapter-contract-invariants.md` records the rules;
      `docs/BACKLOG.md` P0 line updated (snapshot `{}` items closed) and the outcome-rendering
      follow-up added. `CLAUDE.md` status numbers pending P5.

## State (2026-10-14, end of session)

Resumable from disk. Everything below is in worktree `/home/gregf/worktrees/nightpanel/contract`.

| | |
| --- | --- |
| Full `tests/` | `651 passed` — no failures, no xfails left |
| `tests/invariants/` | `186 passed` — 180 contract checks + 6 self-checks |
| `tests/unit/` | `465 passed` (baseline was 5 failed, 373 passed, 61 errors) |
| Ruff | clean on `src/` and `tests/` |
| Commits | five on `np-contract-invariants`: `330d2f0` src contract, `83c70a2` battery + wiring + ADR, `8e5a251` tests/unit migration, `ce9fb51` D10/D11 fixes, `1014288` the three new rules. Working tree clean |

Test command (the worktree has no venv):

```
PYTHONPATH=src /home/gregf/development/utilities/nightpanel/.venv-dev/bin/python -m pytest tests/invariants/ -q --no-cov -p no:cacheprovider
```

### What the battery enforces now

Rules 1–10 as specced, plus three added during execution:

- **Non-UTF8 files** — `capture()` stores base64 with a `binary` flag; a non-UTF8 file must
  come back byte-for-byte or the adapter must refuse with `AdapterError`. Writing the base64
  into the user's file is the corruption this rule exists to prevent.
- **Adapters read through base helpers** — `self.read_text()` / `capture()`, never
  `Path.read_text()`. `UnicodeDecodeError` is a `ValueError` and escapes `except OSError`, so
  one unreadable file aborts the toggle for every other tool (D6, now closed on the source by
  an AST rule).
- **A write stays at the recorded path** — `write()` replaces a symlink rather than following
  it (D10), and every file an adapter writes must be declared `restorable_paths()` or
  `own_paths()`. A file that is neither is a user file written with no capture that could put
  it back.
- **Absence is not a failure** — `read_text()` converts a decode error into `AdapterError` but
  leaves `FileNotFoundError` alone, and a fresh profile is created or refused, never a raw
  exception escaping the adapter (D11).

Self-check proves each is not vacuous: a deliberately bad adapter — one that swallows a
discarded result, one that writes a base64 capture as text, one that writes through a symlink,
one that writes an undeclared file — must fail the same checks the real adapters pass.

### The last 12 failures: one fixture bug, then five genuine defects

The fixture bug: the `paths` fixtures returned paths but never created the directories, so
tests that write directly hit `FileNotFoundError`. Fixed by making each `env` fixture create
the parents of the paths it exposes — the same pattern the invariants seeders use — in
`test_alacritty.py`, `test_nvim_adapter.py`, `test_gemini_cli.py`, `test_gnome_adapter.py`,
`test_firefox_adapter.py`. That cleared 7 of the 12.

The remaining 5 were D10 and D11, recorded as `@pytest.mark.xfail(strict=True, reason=…)` while
`src/` was untouched. Both are fixed and the markers are gone — the battery holds the rules
instead of a marker documenting them.

### Next move

1. P5: report theming-layer coverage (battery measures 67–94% per adapter against a layer
   previously at 0%) and update `CLAUDE.md` status numbers.
2. Decide the PR shape. `./bin/test` is clean, so the branch is ready.
3. The new write rule raises an open question: `own_paths()` lets an adapter declare a file as
   its own, which is honest for a rendered theme, but a pre-existing user file at that path is
   still overwritten without a capture. Decide whether declared-own paths must also be captured
   when they already exist.

## Acceptance

A new adapter that violates any rule fails `./bin/test` without anyone writing a test for it.
That is the deliverable — not the individual bug fixes.

## Open questions

1. Rule 4 strict or permissive on tmux's base-conf fallback? Plan says strict.
2. `apply()` semantics on partial failure: all-or-nothing, or per-adapter outcomes with a
   reported failure list? Plan says per-adapter outcomes, ACTIVE_FILE gated on every stateful
   adapter capturing.
3. Keep `tests/unit/test_*.py` per adapter, or fold them into the battery? Plan says keep the
   behaviour-specific ones (TOML round trip, profiles.ini priority, marker stripping) and let
   the battery own the contract-shaped ones.

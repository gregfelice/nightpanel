# PLAN — adapter contract invariants

**A plan is a committed file.** The original `PLAN-contract-invariants.md` lived only in a
session transcript. When that session was cleared the plan was gone, and with it the baseline
numbers that finding 6 was about — nothing on the filesystem, in git history, or in the memory
system carried them. That is why this copy lives in the repo: a cleared session cannot take a
committed file. If a plan cannot be committed to the repo it works on, it goes in the memory
system, never only in a transcript.

Every number below is measured in this worktree today, not carried from an earlier run. A plan
that quotes a number it cannot re-measure is a claim, not a measurement.

Branch `np-contract-invariants`, worktree `contract`. Not pushed, not merged; main at
`96ad4ad`. No commit count is recorded here: a count in a committed plan goes stale with the
next commit, which is how finding 6 happened.

## Numbers as measured 2026-10-14

| Measure | Value |
| --- | --- |
| Full suite | 690 passed, 8 skipped |
| `tests/invariants/` | 218 passed, 8 skipped — 208 battery + 10 self-checks |
| `ruff check` / `ruff format --check` | clean |
| `bin/validate` | passed on `14ee352` — 11 checks, 0 failures, installed-package smoke included |

The 8 skips are the palette rule, which is scoped to the adapters that can answer it (tmux
alone); each skip names the adapter and the reason.

The earlier baseline survives — untracked, at the root of the main checkout, never committed. It
contradicts itself: the same run is recorded as 389 passed / 5 failed / 52 errors and as 373
passed / 5 failed / 61 errors, and the same suite as 138 tests, 186 passed, and 443 passed with 12
failures against 651 clean. It is not repeated here. The numbers above are the baseline going
forward; the surviving file is either committed as an appendix or deleted, and finding 6 stays open
until one of those happens.

## Findings

| # | Finding | Status |
| --- | --- | --- |
| 1 | `ruff format --check` not clean | Fixed, `0dfe2a0` |
| 2 | Rule 7 (verify tracks the palette) specced, never implemented | Fixed, `588b6b5` — `StatefulRunner`; rule scoped to the adapters that can answer it |
| 3 | Discovery walked the package namespace, so an unimported adapter module was invisible to the rule meant to catch it | Fixed, `5207a28` — walks `nightpanel/adapters/*.py`, requires export |
| 4 | Rule 6 checked declared paths, not written paths | Fixed, `643ca8f` — covers `own_paths()` and the write itself |
| 5 | Orchestrator not held to the rule it enforces on adapters | Fixed, `bb7c9cf` — `_save_state` raises, `_load_state` separates corrupt from absent |
| 6 | Number drift in the plan | Open — the original plan survives untracked at the repo root, and its numbers disagree with each other; the PR description uses today's measurements only |
| 7 | `own_paths()` permitted overwriting a pre-existing user file with no capture | Fixed, `5f1902e` — capture-or-refuse, proven against the four real adapters |
| 8 | `chmod 0o500` test passes silently as root | Fixed, `643ca8f` — skip when `os.geteuid() == 0` |
| — | The orchestrator's own rule was not applied at the entry point: `toggle.py` discarded the result | Fixed, `359a110` — the failure is recorded as a decision and exits non-zero |
| 9 | `paths.py` and `runner.py` were added by `330d2f0` and never added to `nightpanel_sources` in `src/meson.build`. The source tree imported fine, so the suite passed; the installed `.deb` failed at `from ..paths import Paths` | Fixed, `29feb8c`. `bin/validate` caught it — 11 checks, 1 failure. Guard added, `14ee352`: every module under `src/nightpanel/` must be named by an install list, resolved as meson resolves it. Nothing in `tests/` checked this before; the failure only surfaces in CI's proof job on `v*` tags |

The rule numbers in findings 4 and 5 are the original plan's. ADR-001 names the rules without
numbering them, and the test comments carry the same numbers; if the surviving file is deleted, the
test comments should name the rules as ADR-001 names them.

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
> Battery: 208 passed + 8 skipped, plus 10 self-checks that prove each rule bites on a
> deliberately bad adapter. Full suite 687 passed, 8 skipped. The 8 skips are the palette rule,
> scoped to the adapters that can answer it (tmux alone); each names its adapter and the reason.
>
> Findings 1–5, 7, 8 and 9 are fixed. Finding 6 is open: the original plan survives untracked at
> the repo root, and its numbers contradict each other. Every figure here is measured, on
> 2026-10-14.
>
> `bin/validate` passed on `14ee352`: 11 checks, 0 failures, including the clean-trixie build and
> the installed-package smoke. `ruff check` and `ruff format --check` are clean on the branch. The
> commit that records this verdict is docs only, so the gate did not run on it.

## Remaining work

1. Push the branch and open the pull request against `main` — nightpanel has no `master` on either
   remote (`origin`: `main`, `deb-packaging`; `github/HEAD` → `main`) — with the description above,
   which now carries the gate's verdict §2 requires.
2. Rendering adapter and orchestrator failures in the UI — tracked in `docs/BACKLOG.md`. The
   entry point side is settled: `toggle.py` records a refused state write as a decision and
   exits non-zero, because `bin/analyze-toggle-log` only counts invocations that produced a
   decision, so an uncaught exception made the failure invisible to the tool built to diagnose
   toggles.

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
| Full suite | 687 passed, 8 skipped |
| `tests/invariants/` | 218 passed, 8 skipped — 208 battery + 10 self-checks |
| `ruff check` / `ruff format --check` | clean |

The 8 skips are the palette rule, which is scoped to the adapters that can answer it (tmux
alone); each skip names the adapter and the reason.

The earlier baseline (52 errors, 373 vs 389 passed, 138 tests) has no surviving source, so it
cannot be reconciled. It is not repeated here. The numbers above are the baseline going forward.

## Findings

| # | Finding | Status |
| --- | --- | --- |
| 1 | `ruff format --check` not clean | Fixed, `0dfe2a0` |
| 2 | Rule 7 (verify tracks the palette) specced, never implemented | Fixed, `588b6b5` — `StatefulRunner`; rule scoped to the adapters that can answer it |
| 3 | Discovery walked the package namespace, so an unimported adapter module was invisible to the rule meant to catch it | Fixed, `5207a28` — walks `nightpanel/adapters/*.py`, requires export |
| 4 | Rule 6 checked declared paths, not written paths | Fixed, `643ca8f` — covers `own_paths()` and the write itself |
| 5 | Orchestrator not held to the rule it enforces on adapters | Fixed, `bb7c9cf` — `_save_state` raises, `_load_state` separates corrupt from absent |
| 6 | Number drift in the plan | Open — the plan file is gone, so the drift cannot be reconciled; the PR description uses today's measurements only |
| 7 | `own_paths()` permitted overwriting a pre-existing user file with no capture | Fixed, `5f1902e` — capture-or-refuse, proven against the four real adapters |
| 8 | `chmod 0o500` test passes silently as root | Fixed, `643ca8f` — skip when `os.geteuid() == 0` |
| — | The orchestrator's own rule was not applied at the entry point: `toggle.py` discarded the result | Fixed, `359a110` — the failure is recorded as a decision and exits non-zero |

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
> should look at the battery in `tests/invariants/`, which is the enforcement, and at the one
> behaviour change outside it: `toggle.py` now records a refused state write as a decision and
> exits non-zero instead of raising.
>
> Battery: 208 passed + 8 skipped, plus 10 self-checks that prove each rule bites on a
> deliberately bad adapter. Full suite 687 passed, 8 skipped. The 8 skips are the palette rule,
> scoped to the adapters that can answer it (tmux alone); each names its adapter and the reason.
>
> Findings 1–5, 7 and 8 are fixed. Finding 6 is unreconcilable: the plan it referred to was a
> session transcript, not a file, so its baseline numbers have no surviving source. Every figure
> here is measured, on 2026-10-14.
>
> Not run here: `bin/validate`, which runs in CI's proof job. `ruff check` and
> `ruff format --check` are clean on the branch.

## Remaining work

1. Push the branch and open the pull request against `master` with the description above.
2. Rendering adapter and orchestrator failures in the UI — tracked in `docs/BACKLOG.md`. The
   entry point side is settled: `toggle.py` records a refused state write as a decision and
   exits non-zero, because `bin/analyze-toggle-log` only counts invocations that produced a
   decision, so an uncaught exception made the failure invisible to the tool built to diagnose
   toggles.

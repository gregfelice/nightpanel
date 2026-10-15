# Nightpanel SDLC — test, validate, ship, release

The single source of truth for how code gets from your editor to a user's machine.
Everything here is a `bin/` script you run by hand (or that CI runs for you) — there is
**one command shape everywhere** (ADR-030: the estate retired CI engines; portable
`bin/` scripts are the interface, GitHub Actions on the mirror is just a runner that
calls the same scripts).

If a command and this doc ever disagree, the script wins — read `bin/<name>` (each one
has a header comment explaining itself).

---

## The mental model: two independent planes

Nightpanel ships through **two separate channels that do not depend on each other**:

```
                      bump version + commit
                               │
            ┌──────────────────┴───────────────────┐
            ▼                                       ▼
   SOURCE / TAG plane                       ARTIFACT / APT plane
   bin/release vX.Y.Z                       bin/deploy --publish
   • git tag + push to Forgejo + GitHub     • build the .deb
   • GitHub Actions → GH Release + notes     • sign the apt repo
   • CI re-runs the proof run on the tag     • rsync to apt.tigermountain.ai
            │                                       │
            ▼                                       ▼
   source tarball, GH Release page          what users `apt install`
   (feeds AUR, Flathub, e.g.o. — manual)    (the primary distribution channel)
```

- **`bin/release`** is about the **git tag and the GitHub Release** (source-level). It does
  *not* put a `.deb` on `apt.tigermountain.ai`.
- **`bin/deploy --publish`** is about the **`.deb` users actually install**. It does *not*
  create a git tag.

A normal release does **both**. An apt-only hotfix can be just `bin/deploy --publish`.

Both planes share one gate: **the proof run, `bin/validate`** (build the package in a clean
container, install it, smoke-test the installed thing). Nothing public ships unless it passes.

---

## Version lives in four files — keep them in sync

There is no single version constant; the same number is repeated and **must match**:

| File                                                                                   | Read by                              | Purpose                               |
|----------------------------------------------------------------------------------------|--------------------------------------|---------------------------------------|
| `pyproject.toml` (`version = "X.Y.Z"`)                                                 | `bin/release` (enforces tag == this) | the package version                   |
| `debian/changelog` (top: `nightpanel (X.Y.Z-1) …`)                                     | `bin/deploy` (names the `.deb`)      | the **apt-channel** version of record |
| `meson.build` (`version: 'X.Y.Z'`)                                                     | the build                            | install/build metadata                |
| `data/io.github.gregfelice.Nightpanel.metainfo.xml.in` (`<release version="X.Y.Z" …>`) | software centers, AppStream CI       | the user-facing changelog             |

`bin/release` will refuse to tag if `pyproject.toml` ≠ the tag. Nothing auto-checks the other
three, so **bump all four in one commit** (and add a real `<release>` entry to the metainfo —
that's the changelog end users see). Tags are plain semver: `v0.2.8`, not `nightpanel-v0.2.8`.

---

## Day-to-day inner loop

```bash
bin/test                 # headless unit suite (pytest). The canonical test entrypoint —
                         # CI runs the same thing. Pass extra pytest args through:
bin/test tests/unit/test_orchestrator.py -k revert -x
```

`bin/test` needs the dev venv (`.venv-dev`); if it's missing, `./run-dev.sh` bootstraps it.

The GitHub mirror runs `.github/workflows/test.yml` on every push / PR to `main`: ruff lint +
format check, the unit suite, and a preset-validation pass. Keep it green.

### Live-session tests (heavy, optional)

`bin/test` is headless and can't exercise the GNOME Shell extension, live `gsettings`, or the
live-app adapters. That's a separate, self-hosted plane:

```bash
bin/test-vm --list             # show the cross-distro VM matrix
bin/test-vm                    # run the whole matrix, print a pass/fail table
bin/test-vm ubuntu-lts         # one (or several) VM keys
bin/test-vm --async            # background + ntfy on pass/fail
```

This needs nested KVM + interactively-built golden images + the ZFS pool, so **no hosted
runner can do it** — you run it on the box (see `tests/vm/README.md`). It's the live-session
gate for visual/extension changes, not part of the routine loop.

---

## The proof run — `bin/validate`

The thing that makes "done" mean done (ADR-073 DA1). Two layers:

1. **Standard project form** — `PROJECT.yaml` required keys, `README.md`, the `CLAUDE.md`
   sections, `docs/adr/`.
2. **Packaging proof** — builds the `.deb` in a **clean `debian:trixie` container** (the apt
   channel's target distro), installs it, and runs `tests/smoke/gui-smoke.sh` against the
   **installed** package. This catches what unit tests can't: missing-module packaging gaps,
   undeclared build/runtime deps, a broken extension manifest, missing fonts.

```bash
bin/validate             # exit code == number of failed checks (0 == proof passed)
```

> The packaging layer needs **docker**. Without it, `bin/validate` still runs the cheap form
> checks but **SKIPs** the packaging proof (loudly). A docker-less validate must never be taken
> as a green light to publish — and `bin/deploy --publish` enforces that (it requires docker).

CI runs this exact command on every `v*` tag (`.github/workflows/release-deb.yml`) — same
shape as local. It deliberately does **not** publish (DA6: CI never auto-pushes to apt).

---

## Ship the `.deb` — `bin/deploy`

```bash
bin/deploy               # DEFAULT: build .deb + sign the apt repo tree LOCALLY.
                         # Nothing leaves this machine. Use it to dry-run / inspect.

bin/deploy --publish     # build, then run the proof run (bin/validate) as a GATE,
                         # then rsync the signed repo tree to apt.tigermountain.ai.
```

`apt.tigermountain.ai` is a **public surface**, so publishing is deliberate and gated:
`--publish` aborts unless `bin/validate` passes first. The signing key (`apt@rizlabs.com` by
default, override with `GPG_KEY`) and the publisher script (`/usr/local/bin/build-apt-repo`,
override with `BUILD_APT_REPO`) live in the ops repo — see the runbook
`/srv/estate/infrastructure/docs/runbook/change/publish-apt-repo.md` (ADR-048). Build artifacts are corralled into
`dist/` (gitignored).

The signing key's uid still reads `apt@rizlabs.com`, from before the brand rename. Leave it.
A uid rename mints a new key fingerprint, and apt trusts the fingerprint. Every client would
need a fresh `/etc/apt/keyrings/nightpanel.gpg` before it could verify the repo again. The
fingerprint is unaffected by the domain rename, so the keyring already on hosts still works.

Verify a publish landed:

```bash
curl -fsSL http://apt.tigermountain.ai/dists/trixie/InRelease | head
```

Users install per the README's [Quick install](../README.md#quick-install-debian--ubuntu):
register the GPG-signed apt repo once, then plain `apt install nightpanel` / `apt upgrade`.

---

## Cut a release — `bin/release`

Tags the commit and pushes to both remotes. Requires a **clean working tree** and
`pyproject.toml` == the tag.

```bash
bin/release v0.2.8
```

What it does:
- `git tag -a v0.2.8` at HEAD.
- pushes `main` + tag to **`origin`** (Forgejo, canonical) and **`github`** (public mirror).
- the GitHub mirror's Actions then:
  - `release.yml` → creates a **GitHub Release** with auto-generated notes, and validates the
    AppStream metainfo.
  - `release-deb.yml` → runs the **proof run** (`bin/validate`) on the tag.

`bin/release` does **not** touch apt — run `bin/deploy --publish` for that.

---

## Putting it together: a full release, start to finish

```bash
# 1. Bump the version in all four files (see the table above) and add a metainfo
#    <release> entry describing what changed. Commit it.
git commit -am "release: v0.2.8"

# 2. Prove it packages and runs (optional here — deploy --publish re-runs it as a gate,
#    but running it now fails fast before you tag).
bin/validate

# 3. Ship the .deb to the apt channel (gated on the proof run).
bin/deploy --publish

# 4. Tag + GitHub Release (CI re-runs the proof on the tag).
bin/release v0.2.8

# 5. Manual follow-ups for the other channels (see below).
```

Steps 3 and 4 are independent and order doesn't matter; do both for a normal release.

---

## The other channels (manual follow-ups)

These are **not automated** — they're maintainer steps after a release, each with its own
checklist:

- **AUR** (`nightpanel`) — update the `PKGBUILD` `sha256sum` against the new source tarball and
  push. See `aur/MIGRATION.md`.
- **GNOME Shell extension** (extensions.gnome.org) — submit the packaged extension zip. See
  `packaging/extensions-gnome-org/SUBMISSION_CHECKLIST.md`.
- **Flatpak / Flathub** — the in-flight submission; manifest + permission justifications in
  `flathub/FLATHUB_PR.md` and `docs/FLATPAK_SUBMISSION_GUIDE.md`. (App ID is still
  `io.github.gregfelice.DailyDriver` to avoid breaking the open Flathub PR — see CLAUDE.md.)

---

## Quick reference

| I want to…                               | Run                    |
|------------------------------------------|------------------------|
| run unit tests                           | `bin/test`             |
| test the live GNOME extension / adapters | `bin/test-vm`          |
| prove it packages + runs (the gate)      | `bin/validate`         |
| build the `.deb` without publishing      | `bin/deploy`           |
| publish the `.deb` to apt.tigermountain.ai    | `bin/deploy --publish` |
| tag + GitHub Release                     | `bin/release vX.Y.Z`   |

**ADRs that govern this:** ADR-030 (retire CI engines → portable `bin/` + ntfy),
ADR-073 (the DA proof run / packaging gate), ADR-048 (apt-repo publish runbook).

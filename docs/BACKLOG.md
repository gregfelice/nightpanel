# Nightpanel Backlog

Prioritized bugs, features, and improvements.

Two halves:
- **Theming / orchestrator layer** (post-rename) — palette, adapters, GNOME shell extension, Firefox bridge.
- **Keyboard / preset layer** (pre-rename, formerly DailyDriver) — visual keyboard config, presets, cheat sheet.

Scope of latest audit (2026-05-25, extended through 2026-05-26 by testing): orchestrator + adapter layer. `window.py` (1236 lines), `services/theme_service.py` (416 lines), `player_app.py`, and most renderer files **still not audited**.

---

## Distribution / packaging — apt.tigermountain.ai (ADR-048 in /srv/estate/infrastructure)

Ships as a signed `.deb` from the public repo `http://apt.tigermountain.ai` (HTTP +
GPG-signed Release; served by droplet nginx). Live + verified on rivulet
2026-06-10. Forgejo registry stays private. Open follow-ups:

- [ ] **CI auto-publish to apt.tigermountain.ai** — `.forgejo/workflows/release-deb.yml`
  still uploads only to the private Forgejo registry; add a step running
  `/usr/local/bin/build-apt-repo` into `/srv/apt/nightpanel` on `v*` tags
  (needs write access on the droplet runner; the signing key is now vaulted, see below).
- [x] **Signing key → Ansible vault** — DONE 2026-09-05. Stored as
  `nightpanel_apt_signing_key` (plus `..._fpr`) in the ops repo's SOPS-encrypted
  secret store, readable by more than one operator key rather than living on a
  single machine. Restore procedure and the end-to-end proof are in the runbook.
  (ADR-048 checklist.)
- [ ] **GUI applies theme on launch** — starting `nightpanel` runs the orchestrator
  apply path (sets `org.gnome.desktop.interface color-scheme`, kills Nautilus,
  pokes extensions) rather than just opening the config window. Confirm intended —
  the config UI probably shouldn't flip the whole session on open. (`window.py` /
  `services/theme_service.py`, still unaudited.)
- [ ] **HTTPS for apt.tigermountain.ai** (optional, low) — currently HTTP + GPG (signature
  is the trust anchor). Would need the domain added to the SAN cert via
  `ssl_wildcard` on wasa.

---

## Theming layer — P0 (shipping blockers)

**Completed in v0.2.1 → v0.2.5 (private on Forgejo; see [[project-release-state]] for public-push status):**

- [x] **Hardcoded Firefox profile** — replaced with `profiles.ini` discovery in `adapters/firefox.py:find_default_profile()`. (31a86c7)
- [x] **Silent `xpinstall.signatures.required=false` flip** — `install_bridge()` now raises `ConsentRequired` unless `confirmed=True`. (31a86c7)
- [x] **`apply()` lying about state** — only touches `ACTIVE_FILE` if ≥1 adapter succeeded; returns `{adapter: success}` dict. (31a86c7)
- [x] **`_strip_np_block` brittle end-marker** — replaced with paired `/* nightpanel:start */ … /* nightpanel:end */` sentinels. (31a86c7)
- [x] **Dead DailyDriver shell extension at `/extension/`** — deleted. (146ec1f)
- [x] **CLAUDE.md describes DailyDriver** — rewritten for nightpanel post-rename. (146ec1f)
- [x] **CI workflows reference `dailydriver` paths** — swept to `nightpanel`; Snap/Flathub jobs removed. (110ee85, 903ce02)
- [x] **Desktop launcher missing / wrong name** — `Nightpanel.desktop` installed in user prefix + `~/.local/bin/nightpanel` wrapper. (110ee85)
- [x] **Firefox flash on brightness drag** — extension does `insertCSS(new)` before `removeCSS(old)` so the user-origin override never goes empty. (df74eae)
- [x] **`revert()` race window** — `ACTIVE_FILE` unlinked before adapter reverts run so brightness updates mid-revert can't re-engage FF. (a3da2bd)
- [x] **Brightness slider scroll spam** — capture-phase EventControllerScroll consumes wheel events on the slider; 100ms write debounce. (8cb7b96)
- [x] **Multi-instance race** — dropped `NON_UNIQUE` flag; second launches activate existing window. (844aa1a)
- [x] **`GLib.idle_add(orchestrator.apply)` re-firing forever** — apply returns a truthy dict and idle_add interprets that as "stay live." Wrapped in one-shot inner function. (9b6021a) — also captured as [[feedback-glib-idle-add-truthy-return]].
- [x] **Panel button chip background when NP active** — transparent so only NIGHT PANEL text floats on the black panel. (f7df0d5)
- [x] **gws adapter** — new tier-1 adapter writing `theme:Nightpanel` to `~/.gws/todo.state`; matching `Nightpanel` theme added to gws Rust source (`gtd/gws@34c4386`). (fdfe760)

## Theming layer — P0.5 (UX surfacing)

- [ ] **Add gws status row to the Setup tab.** Analogous to the existing Tiling Assistant status row in `views/setup_view.py`. Should show: binary present (`shutil.which("gws")`), state file present (`~/.gws/todo.state`), current theme name from snapshot, and whether NP is currently driving it (`adapter.verify("on")`). Makes the new gws adapter discoverable in the UI rather than buried in the orchestrator's adapter list.

## Theming layer — P1 (correctness / UX)

**Completed:**

- [x] **`pkill nautilus` on every gtk.css apply/revert** — removed. Nautilus colors update on next launch rather than immediately; tradeoff is no destroyed tab state. (31a86c7)
- [x] **Dead `extension/` dir + Firefox XPI rebuild** — dead dir gone; XPI rebuild is a tracked Python one-liner in commit history; live source at `src/nightpanel/shell-extension/`. (5ccb1f7, df74eae)
- [x] **Orchestrator state-machine tests** — 5 happy-path tests covering snapshot guard + apply outcomes + revert. (903ce02)

**Still open:**

- [ ] **Singleton dbus race** — dropping `NON_UNIQUE` mostly fixes duplicates, but TWO instances can still spawn if launched fast enough that neither has registered the dbus name yet. Observed during the Alt+Super+/ binding test on 2026-05-26: two `--cheat-sheet` instances, the second ended up owning the dbus name. Use a startup lock file (or accept the rare race).
- [ ] **No lock on concurrent `apply()`/`revert()`** — singleton mitigates most cases but rapid panel-button + in-app-toggle races still possible. `fcntl.flock` on `nightpanel-state.json` would close it.
- [ ] **No surfacing of adapter failures to the UI** — `apply()` now returns outcomes but nothing renders them. Need `np-status.json` + extension JS to display per-adapter state in the panel button.
- [x] **`TmuxAdapter.snapshot()` / `FirefoxAdapter.snapshot()` return `{}`** — DONE 2026-10-14, committed on `np-contract-invariants` (`330d2f0`). Both capture real pre-state: tmux captures the live value of every option the overlay sets, firefox records the profile it writes into. `revert({})` on a stateful adapter now raises instead of guessing, and the battery in `tests/invariants/` refuses any adapter that reverts on an empty snapshot — see `docs/adr/001-adapter-contract-invariants.md`.
- [x] **Per-adapter happy-path tests** — DONE 2026-10-14, committed on `np-contract-invariants` (`588b6b5`). The contract battery (`tests/invariants/`) runs seed → capture → apply → verify("on") → revert → verify("off") against every `Adapter` subclass discovered by walking the module files in `nightpanel/adapters/`, so a new adapter gets the happy path without anyone writing it. Behaviour-specific tests stay per adapter. The palette half is scoped to the adapters that can answer it: only a `verify()` that reads the palette can tell that a *different* palette was applied, and today that is tmux alone. The other eight verify on a sentinel, a theme name, or an import line, so they answer "on" for every palette — a legitimate answer for them, but not one this rule can check. They skip with the reason named, because a rule that checks nothing is a preference.
- [ ] **Render the outcomes the contract now produces** — `apply()`/`revert()` return per-adapter outcomes, and `AdapterNotLive` is a third state (the tool was not running, the file half landed). Nothing renders it: the panel button and `np-status.json` need to show per-adapter state, and the journal should record "not live" separately from "failed". Follows from `docs/adr/001-adapter-contract-invariants.md`.

## Theming layer — P2 (DX / code quality)

- [ ] **Extract shared helpers** — `_run`, `_gsettings_get/set`, `~/.config/nightpanel` path construction duplicated across 5+ files. Single `_util.py` + `_paths.py`.
- [x] **Inject paths into adapters** — DONE 2026-10-14 (`330d2f0`). `Paths` is injected through the constructor, so no adapter binds the developer's home at import time and every test runs in a fixture root. Module-level `Path.home()` and absolute path literals are refused by the battery.
- [ ] **`Adapter.name` class-attr without default + no ABC enforcement** — forgetting to set `name` is a runtime `AttributeError`. Default + check or `__init_subclass__`.
- [x] **`_load_state()` swallows JSON decode errors** — DONE 2026-10-14 (`bb7c9cf`). A corrupt state file now raises `OrchestratorError` and reports as "state file corrupt" rather than as nine per-adapter "no capture" failures, and `_save_state()` raises on a failed write so `nightpanel-active` is never marked without a baseline behind it. The toggle path now records that failure as an outcome instead of crashing with a traceback; rendering it in the UI is still open above.
- [ ] **`np-host.py` 500ms busy-poll** — replace with `Gio.FileMonitor` / `inotify` for true event-driven forwarding.
- [ ] **`np-host.py` filename hyphen + copied-to-config detachment** — rename to `np_host.py`; consider not copying (run from src tree via wrapper).
- [ ] **`nvim` adapter socket discovery** — patterns may miss modern nvim's `$XDG_RUNTIME_DIR/nvim.<pid>.0`. Validate.
- [ ] **`install_gnome_extension()` always enables** — coercive if user explicitly disabled. Check disabled-list first.
- [ ] **`bin/release` private/public mode** — currently the private flow (Forgejo-only) is manual; add a `--private` flag that skips the `git push github`.

---

## Keyboard layer — P0 (legacy DailyDriver)

- [ ] Screenshots for hypr / apple presets don't work — should show equivalent of apple keys
- [ ] `<D-/>` shortcut bug (slash key with Super modifier)

**Completed:**
- [x] **Green the whole suite (2026-06-02)** — full `tests/` now 298 passing, 5× stable, hermetic, `ruff` clean (was ~44 failures + a process-killing segfault). The ~34 `test_gsettings` failures were mostly stale mocks (backend moved to a composite schema source + `Gio.Settings.new_full`); rewrote them and made them host-independent via a `glob→[]` fixture so they no longer pass/fail based on installed GNOME extensions. Same composite-source fix applied to `test_tiling`; `test_keyboard_config` pkexec tests now mock the hid_apple shell-out. See "source regressions" + "segfault" + "toggle" notes below.
- [x] Test compositor shortcut grabs on a live GNOME Wayland session (tested, doesn't work — GNOME limitation)
- [x] Update `ShortcutGrabberService` to detect GNOME and skip portal attempt
- [x] AppStream metainfo (`data/io.github.gregfelice.Nightpanel.metainfo.xml.in`) finalized
- [x] Alt+Super+/ keybinding repointed at `nightpanel --cheat-sheet` (was pointing at a stale `dailydriver-standalone` path)

## Keyboard layer — P1

- [ ] **AppStream metainfo screenshots fail validation** — `release.yml` workflow's `validate-appstream` job fails with: `<release>` version duplicated, screenshot heights >900px (max), images have padding. Cosmetic; release publishing succeeds independently.
- [ ] Research GNOME Shell extension approach for global shortcuts (extension exposes GrabAccelerator via custom D-Bus interface)
- [ ] Background daemon mode (`nightpanel --daemon`) for persistent shortcut grabs
- [ ] Make detection methods on `GSettingsService` public (`_detect_terminal` etc.)
- [ ] Add "Shortcut Status" indicator in UI showing compositor grab state + GNOME limitation warning
- [ ] Integration test for `ShortcutGrabberService` with `python-dbusmock`
- [ ] GNOME schema version validation in preset tests
- [ ] Add CI status badge once test_gsettings is green

## Keyboard layer — P2

- [ ] KDE Plasma backend
- [ ] Hyprland backend
- [ ] GNOME Circle application
- [ ] Auto-update checker
- [ ] Per-shortcut compositor grab toggle in UI
- [ ] Monitor GNOME GlobalShortcuts portal implementation progress
- [ ] Custom shortcut editor (create new shortcuts)
- [ ] Import/export profiles

## Keyboard layer — P3

- [ ] COPR (Fedora) packaging
- [ ] Keyboard layout auto-detection (QWERTY/Dvorak/Colemak)
- [ ] User-contributed preset marketplace / sharing
- [ ] Multi-monitor shortcut awareness

---

## Testing infrastructure (the path to "done with quality issues")

Layered plan (agreed 2026-06-02). The instinct toward VM-based stress testing is right, but it's step 4, not step 1 — building it first inverts the cost/value order.

1. [x] **Green the suite** — done 2026-06-02 (298 passing, stable, hermetic). A red suite can't tell a regression from pre-existing breakage; greening already surfaced 3 live source regressions (below).
2. [ ] **Cheap logic-bug wins** — the enumerated tier-1 bugs (`_load_state()` JSON-decode swallow, `TmuxAdapter`/`FirefoxAdapter.snapshot()` returning `{}`, etc.). Pure unit tests, no environment.
3. [ ] **NEXT ROUND — spike the ephemeral-isolated-env stress loop.** HOME/XDG → tmpdir, `dbus-run-session` + `python-dbusmock`, then hammer apply/revert/toggle thousands of times with randomized timing to flush out the dbus singleton race + concurrent `apply()`/`revert()` race (both still open under Theming P1). Runs in the GitHub-mirror CI, nothing to babysit. We now have a real `bin/nightpanel-toggle` to hammer.
   - **Open question to settle during the spike:** can `gnome-shell --headless --virtual-monitor` (or a nested compositor) actually load/exercise the panel extension in CI? If not, extension testing falls into the VM tier.
4. [ ] **VM** — only for the visual + shell-extension residue (Firefox flash, panel button, event re-dispatch). Must be scripted (libvirt/QEMU + `virsh snapshot` revert + SSH runner), not click-driven, or it's a safer manual bench, not automation. Heavy on this box (OOM history + og-llama ~24GB) — runs when og-llama is down.

### Source regressions the green-up uncovered (fixed 2026-06-02, in source, not by bending tests)

All from the `dailydriver→nightpanel` rename (`9e5f57c`) — a "guilty source" sweep that silently lowercased/renamed live behavior:

- [x] **Shortcut names rendered lowercase** in the keyboard UI — `_humanize_key_name` (backends/gnome.py) lost its Title-Case block (`"left half"` not `"Left Half"`).
- [x] **Setup status messages + keybinding names lowercased** — `"added: kitty"` / `"launch terminal"` → `"Added: kitty"` / `"Launch Terminal"`.
- [x] **Flatpak cheat-sheet detection broken** — `detect_nightpanel()` queried `io.github.gregfelice.Nightpanel`, but the published App ID is still `…DailyDriver` (manifest deliberately kept). The Flatpak launch path silently never matched. Restored to `DailyDriver`.

### Other testing work done 2026-06-02

- [x] **`nightpanel-toggle` debounce script implemented** — was a half-landed feature (`fdae607` committed the tests + `bin/analyze-toggle-log` but never the script; deployed copy was a simple apply/revert). Wrote `bin/nightpanel-toggle` (repo is its home, per Platform Compliance). Debounce = accidental-double-click guard capped ~200ms (a longer window is the "click twice to toggle" bug); `NP_DEBOUNCE_S` only tightens it. Deployed via a dotfiles-stow symlink → repo `bin/`; script self-resolves `NIGHTPANEL_HOME` from its own path so it survives project moves. See [[toggle-script-deployment]].
- [x] **`test_profiles` segfault fixed** — was a test hitting real dconf via `Gio.Settings.new()`; GIO isn't fork-safe so pytest-forked *caused* the crash. Fix was mocking `Gio` (hermetic). `active_profile` reading `current-preset` from dconf is intentional (UI writes it); the integration `active_profile == preset` assertion was a stale service-level test of UI responsibility — fixed test-side, `apply_profile` left correctly pure.

**Notes / loose ends:**
- **dotfiles-stow has an uncommitted change** — the deployed `nightpanel-toggle` regular file was replaced with a symlink into this repo; the old 36-line script was removed (tracked, recoverable). Commit/keep is Greg's call.
- **Unused `active-profile` gschema key** sits next to the used `current-preset` — naming smell, cleanup someday.

---

## Distribution (queued, see [[project-release-state]])

- [ ] **Forgejo repo rename** `dailydriver-standalone` → `nightpanel` (Greg does in web UI; no API token available)
- [ ] **GitHub public push** of v0.2.2 → v0.2.5 tags (`git push github main v0.2.5`; GH Actions auto-publishes Releases)
- [ ] **AUR migration** — orphan `dailydriver`, push fresh `nightpanel` package. PKGBUILD + .SRCINFO ready in `aur/`; instructions in `aur/MIGRATION.md`.
- [ ] **extensions.gnome.org submission** — ZIP + compliance checklist ready in `packaging/extensions-gnome-org/`. User clicks submit (per [[feedback-submission-carefulness]]).
- [ ] **Flathub** — deprioritized after the prior moderator interaction; the manifest `io.github.gregfelice.DailyDriver.yml` at repo root is stale.

---

## Lessons learned (incidents → memories)

- **GLib.idle_add truthy-return trap** — see [[feedback-glib-idle-add-truthy-return]]. Burned ~90 minutes on 2026-05-26 because I'd changed `orchestrator.apply()` to return a dict (state-machine fix) and the idle_add callback at `window.py:58` had no return-False wrapper. Lock this in next time you change any method's return type that's bound to a GLib source.
- **Submission carefulness** — see [[feedback-submission-carefulness]]. Flathub PR went bad after a missed guideline + hostile moderator; prep + checklist, never auto-submit.

---

## Out of scope of latest audit (worth a follow-up pass)

- `src/nightpanel/window.py` (1236 lines) — handles all views; almost certainly wants splitting.
- `src/nightpanel/services/theme_service.py` (416 lines) — relationship to the new palette/renderer layer unclear; possible duplication.
- `src/nightpanel/player_app.py` (304 lines) — Spotify player UI; not audited.
- `src/nightpanel/renderers/{alacritty,emacs,firefox_chrome,player,tmux}.py` — only `gtk.py` was read.

> **Estate project** — follows the estate standard work: the law is
> `/srv/estate/CLAUDE.md` (= `/srv/estate/docs/05-estate-operating-rules.md`).
> Read it first; only local deltas below.

# Nightpanel

Two halves under one repo (post-rename from DailyDriver, commit `9e5f57c`):

1. **Theming / orchestrator layer** — palette-driven dark-mode that reaches across alacritty, tmux, nvim, emacs, claude-code, GNOME (GTK CSS + background), and Firefox. Toggled via a GNOME Shell panel button or `~/.local/bin/nightpanel-toggle`.
2. **Keyboard / preset layer** (legacy DailyDriver) — visual keyboard config for GNOME/Wayland with presets, cheat sheet overlay, conflict detection.

## Quick Reference

project_type: tool

```bash
# Dev environment (repo lives at /srv/data/development/nightpanel)
cd /srv/data/development/nightpanel
source .venv-dev/bin/activate     # if missing: uv venv .venv-dev --system-site-packages
PYTHONPATH=src pytest tests/unit/ -v

# Lint (ruff isn't yet wired into the dev venv — install with `uv pip install ruff`)
ruff check src/ tests/
ruff format src/ tests/

# Build Flatpak locally
flatpak-builder --force-clean --user --install build-dir io.github.gregfelice.DailyDriver.yml

# Run the keyboard-config UI
./run-dev.sh

# Toggle the theming layer
~/.local/bin/nightpanel-toggle
```

## Architecture

GTK4/Libadwaita app using Python + Meson. Packaged as Flatpak. A separate GNOME Shell extension (ES module) lives alongside the app and surfaces a panel button.

```
src/nightpanel/
  application.py          # App entry point, GtkApplication subclass
  window.py               # Main window (1236 lines — needs splitting; handles all views)
  palette.py              # Single source of truth for theming colors (17 semantic slots)
  models/                 # shortcut.py, keyboard.py, profile.py
  adapters/               # Theming layer — one class per nightpanel-aware tool
    base.py               # Adapter ABC: installed/snapshot/apply/revert/verify
    alacritty.py          # config-file flip via `import =`
    tmux.py               # source-file overlay
    nvim.py               # after/plugin lua + --remote IPC
    emacs.py              # theme file + emacsclient eval
    claude_code.py        # settings.json flip + COLORTERM-stripping wrapper
    gnome.py              # gsettings color-scheme + background + GTK CSS
    firefox.py            # native-messaging command file + userChrome.css
  renderers/              # Per-tool config text generated from Palette
    alacritty.py, emacs.py, firefox_chrome.py, gtk.py, player.py, tmux.py
  services/
    nightpanel_orchestrator.py  # Drives the adapter list on apply()/revert()
    np-host.py                  # Firefox native-messaging host (standalone script)
    gsettings_service.py        # GNOME keyboard shortcut R/W (keyboard half)
    keyboard_config_service.py  # Orchestrates shortcut configuration
    profile_service.py          # Preset profile TOML I/O
    hardware_service.py         # Keyboard hardware detection
    hid_apple_service.py        # Mac keyboard hid-apple module config
    theme_service.py            # (relationship to renderers/ unclear; possible duplication)
    backends/                   # Desktop-specific backends (GNOME, KDE/Hyprland TBD)
  views/                  # keyboard_view, cheatsheet, preset_selector, shortcut_editor, shortcut_list
  player_app.py           # Spotify mini-player (theme-synced)
```

Key patterns:
- **Adapter contract** is the integration surface — add a new tool by writing a subclass and appending it to `NightpanelOrchestrator.adapters`. State is namespaced under `adapter.name` in `~/.config/nightpanel/nightpanel-state.json`.
- **Palette is the single source of truth** for theming colors. Every adapter consumes a `Palette` instance; no hardcoded hexes in adapters.
- **GTK** requires `gi.require_version()` before imports; ruff E402 is suppressed for those files.
- **Services are injected into views**, not imported directly.
- **Presets are TOML** files defining complete shortcut sets ("clean slate" approach).
- **GNOME does NOT support xdg-desktop-portal GlobalShortcuts**; app falls back to gnome-settings-daemon keybindings (keyboard half).

## Conventions

- **Python**: 3.11+, ruff for linting/formatting, line length 100
- **Testing**: pytest with pytest-cov, pytest-mock; tests in `tests/unit/` and `tests/invariants/` (the adapter-contract battery, ADR-001). The theming layer is covered: adapters 91–100%, renderers 100%, orchestrator 54%, `theme_service.py` 33%.
- **Packaging**: Use `uv pip` for installs, Meson build system, Flatpak for distribution
- **Git**: Conventional commit messages. Forgejo canonical; GitHub is public mirror (see Platform Compliance).
- **Branching**: main branch
- **App ID**: `io.github.gregfelice.DailyDriver` (reverse DNS for Flatpak/Flathub — kept for now to avoid breaking the in-flight Flathub PR; rename to `io.github.gregfelice.Nightpanel` is a separate decision)

## Key Documentation

- `docs/BACKLOG.md` — Prioritized backlog (P0-P3) for both layers
- `docs/SDLC.md` — **the** end-to-end flow: test → validate → deploy → release (and the `bin/` scripts)
- `docs/FLATPAK_SUBMISSION_GUIDE.md` — Flathub submission reference
- `docs/adr/` — Architecture decision records
- `docs/research/` — Research notes
- `flathub/FLATHUB_PR.md` — Flathub PR description and permission justifications

## Status

**v0.1.0-alpha** — Keyboard half is solid on GNOME/Wayland. Theming layer is functional but has known correctness/portability bugs flagged in `docs/BACKLOG.md` (hardcoded Firefox profile, brittle gtk.css strip, snapshot-corruption risk, etc.).

- 698 tests passing, 8 skipped (the palette rule, scoped to tmux); none failing
- 49% total coverage (2026-10-20); the keyboard views and `window.py` are the weakest
- Flathub submission pending review
- AUR package published
- Known platform limitation: GNOME does not implement xdg-desktop-portal GlobalShortcuts

See `docs/BACKLOG.md` for current priorities.

## Platform Compliance

Per **ADR-030** (`~/foundations/docs/adr/030-retire-woodpecker-slim-forgejo.md`) the estate has retired all CI engines. Forgejo is git-only; each repo standardizes on portable `bin/` scripts; async feedback via ntfy. The pattern transfers to public-shipping projects like nightpanel: GitHub Actions on the mirror acts as a free runner that calls the same portable scripts.

- [x] GitHub Actions workflow exists (`.github/workflows/`) — currently lint + test + Flatpak build inline
- [ ] Write `bin/test`, `bin/test-async`, `bin/deploy`, `bin/release VERSION` per `~/foundations/templates/sdlc/`
- [ ] Refactor `.github/workflows/` to call `bin/` scripts instead of inlining build steps
- [ ] Set up Forgejo push-mirror to `github.com/gregfelice/nightpanel` (public discoverability + Issues)
- [ ] `bin/release` dual-publishes to Forgejo Releases + GitHub Releases on tag
- [ ] ntfy notification on long test runs (`bin/test-async`)

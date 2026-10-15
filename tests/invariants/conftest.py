# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures for the contract battery.

Every adapter is constructed with an injected ``Paths`` rooted at ``tmp_path``
and a ``RecordingRunner``, so nothing the battery does touches the developer's
real home. Adapters are discovered by walking ``nightpanel.adapters``: a new
adapter is picked up by the battery without anyone writing a test for it, which
is the mechanism that keeps this class of bug out.

Seeders build plausible *pre-existing* user state — the state a real user would
have had before nightpanel touched it. Without a seed there is nothing to
capture, and a round-trip test that starts from an empty file proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from nightpanel.adapters import Adapter
from nightpanel.palette import NIGHTPANEL, Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

USER_CONTENT = "user content — nightpanel must put this back\n"


def all_adapters() -> list[type[Adapter]]:
    """Every Adapter subclass exported by nightpanel.adapters."""
    import nightpanel.adapters as pkg

    found = {
        obj
        for obj in vars(pkg).values()
        if isinstance(obj, type) and issubclass(obj, Adapter) and obj is not Adapter
    }
    return sorted(found, key=lambda c: c.name)


# ── Seeders ────────────────────────────────────────────────────────
#
# A seeder builds the fixture for one adapter. The generic seeder writes plain
# text at every declared path, which is enough for any adapter that does not
# parse its own format. Adapters that parse a format need content that parses.

def _seed_generic(a: Adapter, env: Env) -> None:
    for p in a.restorable_paths():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(USER_CONTENT)


def _seed_alacritty(a: Adapter, env: Env) -> None:
    cfg = a.restorable_paths()[0]
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '[general]\nimport = [\n    "themes/themes/ayu_dark.toml"\n]\n\n[window]\npadding = { x = 12, y = 12 }\n'
    )


def _seed_claude_code(a: Adapter, env: Env) -> None:
    settings, wrapper = a.restorable_paths()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"theme": "blue"}\n')
    # The installer's symlink, which the adapter replaces with a wrapper.
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    target = env.root / "real-bin"
    target.write_text("#!/bin/sh\nexec real-claude \"$@\"\n")
    target.chmod(0o755)
    wrapper.symlink_to(target)


def _seed_gemini_cli(a: Adapter, env: Env) -> None:
    settings, wrapper = a.restorable_paths()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"ui": {"theme": "github"}}\n')
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(USER_CONTENT)
    wrapper.chmod(0o755)


def _seed_gws(a: Adapter, env: Env) -> None:
    state = a.restorable_paths()[0]
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("theme:Catppuccin\ncat:inbox\nproj:estate\n")


def _seed_nvim(a: Adapter, env: Env) -> None:
    lua = a.restorable_paths()[0]
    lua.parent.mkdir(parents=True, exist_ok=True)
    lua.write_text("vim.cmd('colorscheme tokyonight')\n")


def _seed_gnome(a: Adapter, env: Env) -> None:
    for p in a.restorable_paths():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("/* the user's own gtk.css */\nwidget { color: #123456; }\n")


def _seed_firefox(a: Adapter, env: Env) -> None:
    root = env.root / ".mozilla" / "firefox"
    profile = root / "abcd1234.default"
    (profile / "chrome").mkdir(parents=True, exist_ok=True)
    (root / "profiles.ini").write_text(
        "[Profile1]\nPath=abcd1234.default\nName=default\nDefault=1\n"
    )
    for p in a.restorable_paths():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(USER_CONTENT)


# What a *live session* would answer. Without this the battery can only test the
# "no session running" case, and an adapter whose capture depends on a live tool
# looks like it captured nothing.
LIVE_ANSWERS = {
    "tmux": {("tmux", "show", "-gv"): "user-value"},
    "emacs": {("emacsclient", "-e"): "t"},
    "nvim": {("nvim", "--server"): "tokyonight"},
    "gnome": {("gsettings", "get"): "'user-value'"},
    "firefox": {("gsettings", "get"): "0.9"},
}

# What makes a tool *live* in the fixture: a socket or session the adapter can
# find. Without it an adapter that only acts on a running instance does nothing,
# and a failure test that expects a raise is testing the wrong thing.
LIVE_FIXTURES = {
    "nvim": lambda env: (
        env.paths.runtime.mkdir(parents=True, exist_ok=True),
        (env.paths.runtime / "nvim.1234.0").touch(),
    ),
}

SEEDERS = {
    "alacritty": _seed_alacritty,
    "claude_code": _seed_claude_code,
    "gemini_cli": _seed_gemini_cli,
    "gnome": _seed_gnome,
    "gws": _seed_gws,
    "nvim": _seed_nvim,
    "firefox": _seed_firefox,
}


# ── Fixtures ───────────────────────────────────────────────────────


@dataclass
class Env:
    """A hermetic world: one fixture root, the paths inside it, a fake live session."""

    root: Path
    paths: Paths
    runner: RecordingRunner
    palette: Palette = NIGHTPANEL

    def seed(self, adapter: Adapter) -> None:
        (SEEDERS.get(adapter.name) or _seed_generic)(adapter, self)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(root=tmp_path, paths=Paths(home=tmp_path), runner=RecordingRunner())


@pytest.fixture
def adapter(env: Env, request) -> Adapter:
    """An adapter built against the fixture, with plausible user state seeded and a
    live session answering its probes."""
    cls = request.param
    env.runner = RecordingRunner(prefix=LIVE_ANSWERS.get(cls.name))
    a = cls(paths=env.paths, runner=env.runner, palette=env.palette)
    env.seed(a)
    (LIVE_FIXTURES.get(cls.name) or (lambda e: None))(env)
    return a

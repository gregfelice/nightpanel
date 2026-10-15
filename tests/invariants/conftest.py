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

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from nightpanel.adapters import Adapter
from nightpanel.palette import NIGHTPANEL, Palette
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

USER_CONTENT = "user content — nightpanel must put this back\n"


def adapters_in_package(pkg) -> list[type[Adapter]]:
    """What the package namespace shows — only what was imported."""
    return sorted(
        {
            obj
            for obj in vars(pkg).values()
            if isinstance(obj, type) and issubclass(obj, Adapter) and obj is not Adapter
        },
        key=lambda c: c.name,
    )


def adapters_from_files(pkg) -> list[type[Adapter]]:
    """Every Adapter subclass defined by a module in the package directory.

    Walked from the files, not from the namespace. Reading the namespace would
    miss exactly the miss this battery exists to catch: an adapter module written
    and never imported, which is invisible to every check here, including the
    registration check.
    """
    found = set()
    for root in pkg.__path__:
        for file in sorted(Path(root).glob("*.py")):
            module = importlib.import_module(f"{pkg.__name__}.{file.stem}")
            found.update(
                obj
                for obj in vars(module).values()
                if isinstance(obj, type) and issubclass(obj, Adapter) and obj is not Adapter
            )
    return sorted(found, key=lambda c: c.name)


def all_adapters() -> list[type[Adapter]]:
    """Every Adapter subclass exported by nightpanel.adapters."""
    import nightpanel.adapters as pkg

    return adapters_from_files(pkg)


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
    # The installer's layout, not just the symlink. The adapter only installs its
    # wrapper when versions/ exists, so a fixture with the symlink and no versions
    # dir makes apply() skip the wrapper — and verify("on") can then never be true,
    # for a reason that has nothing to do with the adapter.
    versions = env.root / ".local" / "share" / "claude" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / "claude"
    target.write_text('#!/bin/sh\nexec real-claude "$@"\n')
    target.chmod(0o755)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
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

USER_VALUE = "user-value"


class StatefulRunner(RecordingRunner):
    """A fake tool that remembers what was set, so verify() can be checked.

    RecordingRunner answers from a canned table, which can never make verify("on")
    true after an apply: the adapter wrote something and the fake never looked at
    it. This models the four tools whose state is a key/value store — gsettings,
    tmux, nvim, emacs — so a write is visible to the next read.

    tmux is the case that forced it: its apply() does not set options, it writes an
    overlay and sources it, so the model has to read the file the adapter wrote.
    That is what the real tmux does.

    It is a model of four tools, not a proof about the world. It shows that an
    adapter's verify() agrees with what the adapter itself wrote. Whether a real
    tmux sources an overlay, or a real gsettings propagates, is the VM's job.
    """

    def __init__(self, initial: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state: dict = dict(initial or {})

    def run(self, cmd: list[str]) -> CompletedProcess[str]:
        self.calls.append(list(cmd))
        key = tuple(cmd)
        answered = key in self.answers or any(key[: len(p)] == p for p in self.prefix)
        if self.fail_all or key in self.fail or (self.no_session and not answered):
            return CompletedProcess(cmd, 1, "", f"{cmd[0]}: not running")
        value = self._handle(cmd)
        if value is None:
            return CompletedProcess(cmd, 0, self.answers.get(key, ""), "")
        return CompletedProcess(cmd, 0, value, "")

    def _handle(self, cmd: list[str]) -> str | None:
        """What the modelled tool would answer. None means not a modelled command."""
        if cmd[0] == "gsettings" and len(cmd) >= 4:
            slot = (cmd[2], cmd[3])
            if cmd[1] == "get":
                return self.state.get(slot, USER_VALUE)
            if cmd[1] == "set" and len(cmd) >= 5:
                self.state[slot] = cmd[4]
                return ""

        if cmd[0] == "tmux":
            if cmd[1:3] == ["show", "-gv"]:
                return self.state.get(("tmux", cmd[3]), USER_VALUE)
            if cmd[1:3] == ["set", "-g"]:
                self.state[("tmux", cmd[3])] = cmd[4]
                return ""
            if cmd[1] == "source-file":
                return self._source(Path(cmd[2]))

        if cmd[0] == "nvim" and "--remote-expr" in cmd:
            return self.state.get(("nvim", cmd[cmd.index("--remote-expr") + 1]), USER_VALUE)
        if cmd[0] == "nvim" and "--remote-send" in cmd:
            match = re.search(r"colorscheme (\S+)", cmd[cmd.index("--remote-send") + 1])
            if match:
                self.state[("nvim", "g:colors_name")] = match.group(1)
            return ""

        if cmd[0] == "emacsclient":
            expr = cmd[-1]
            enabled = self.state.setdefault(("emacs", "themes"), set())
            match = re.search(r"\(quote (\w+)\)", expr)
            theme = match.group(1) if match else None
            if "load-theme" in expr:
                enabled.add(theme)
                return "t"
            if "disable-theme" in expr:
                enabled.discard(theme)
                return ""
            if "custom-enabled-themes" in expr:
                return str(sorted(enabled))

        return None

    def _source(self, overlay: Path) -> str:
        """Apply the overlay the way tmux would: read it, set what it sets.

        The adapter never calls `tmux set` for these options — it writes a file and
        sources it. The only way the model can know what the adapter wrote is to read
        the file, which is what the real tmux does.
        """
        for line in overlay.read_text().splitlines():
            match = re.fullmatch(r"set(?:-window-option)? -g (\S+)\s+\"?([^\"]*)\"?", line)
            if match:
                self.state[("tmux", match.group(1))] = match.group(2)
        return ""


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

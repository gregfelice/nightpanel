# SPDX-License-Identifier: GPL-3.0-or-later
"""FirefoxAdapter — profiles.ini discovery, command-file flip, userChrome.css.

Two surfaces:
  - ``find_default_profile()``: mirror Firefox's own profile-selection order
    (Install Default → Profile Default=1 → first Profile).
  - apply()/revert()/verify(): write the command JSON the native host polls,
    and (re)materialize userChrome.css + the legacy-stylesheets pref.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the profile is discovered through
the fixture's own profiles.ini, the brightness values come from the runner's
answers, and revert is exercised with a real snapshot — the old tests called
``revert({})``, which under the contract is a guess the adapter refuses.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.firefox import FirefoxAdapter, find_default_profile
from nightpanel.palette import NIGHTPANEL
from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

# ── find_default_profile ────────────────────────────────────────────────


def _write_ini(root, body: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.ini").write_text(body)


def test_find_profile_missing_ini_returns_none(tmp_path):
    assert find_default_profile(tmp_path) is None


def test_find_profile_prefers_install_default(tmp_path):
    _write_ini(
        tmp_path,
        "[Install123]\nDefault=abc.default-release\n\n[Profile0]\nPath=zzz.other\nDefault=1\n",
    )
    assert find_default_profile(tmp_path) == tmp_path / "abc.default-release"


def test_find_profile_falls_back_to_default_flag(tmp_path):
    _write_ini(
        tmp_path,
        "[Profile0]\nPath=aaa.first\n\n[Profile1]\nPath=bbb.default\nDefault=1\n",
    )
    assert find_default_profile(tmp_path) == tmp_path / "bbb.default"


def test_find_profile_falls_back_to_first_profile(tmp_path):
    _write_ini(tmp_path, "[Profile0]\nPath=aaa.first\n\n[Profile1]\nPath=bbb.second\n")
    assert find_default_profile(tmp_path) == tmp_path / "aaa.first"


def test_find_profile_absolute_path_respected(tmp_path):
    abs_dir = tmp_path / "elsewhere" / "prof"
    _write_ini(tmp_path, f"[Profile0]\nPath={abs_dir}\nDefault=1\n")
    assert find_default_profile(tmp_path) == abs_dir


# ── apply / revert / verify ─────────────────────────────────────────────


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """The fixture root is not pre-created, so the directories the tests write into
    are made here — the same as the invariants seeders do."""
    ff_root = paths.home / ".mozilla" / "firefox"
    profile = ff_root / "abc.default"
    profile.mkdir(parents=True, exist_ok=True)
    command_file = paths.nightpanel / "np-command.json"
    command_file.parent.mkdir(parents=True, exist_ok=True)
    _write_ini(ff_root, "[Profile0]\nPath=abc.default\nDefault=1\n")

    # Brightness is read through the Runner, so the answers pin it here.
    runner.answers[("gsettings", "get", "io.github.gregfelice.Nightpanel", "theme-brightness")] = (
        "0.7"
    )
    runner.answers[("gsettings", "get", "io.github.gregfelice.Nightpanel", "video-brightness")] = (
        "0.4"
    )
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        command_file=command_file,
        ff_root=ff_root,
        profile=profile,
    )


def test_apply_writes_command_with_both_brightnesses(env):
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)
    cmd = json.loads(env.command_file.read_text())
    assert cmd["action"] == "apply"
    assert cmd["brightness"] == 0.7
    assert cmd["videoBrightness"] == 0.4


def test_apply_materializes_userchrome_and_pref(env):
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(NIGHTPANEL)
    user_chrome = env.profile / "chrome" / "userChrome.css"
    user_js = env.profile / "user.js"
    assert user_chrome.exists()
    assert "nightpanel" in user_chrome.read_text().lower()
    assert "toolkit.legacyUserProfileCustomizations.stylesheets" in user_js.read_text()


def test_apply_does_not_duplicate_pref(env):
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    adapter.apply(NIGHTPANEL)
    adapter.apply(NIGHTPANEL)
    pref_line = 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets"'
    assert (env.profile / "user.js").read_text().count(pref_line) == 1


def test_verify_tracks_command_action(env):
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    assert adapter.verify("off") is True  # no command file yet
    snap = adapter.snapshot()
    adapter.apply(NIGHTPANEL)
    assert adapter.verify("on") is True
    adapter.revert(snap)
    assert adapter.verify("off") is True
    assert adapter.verify("on") is False


def test_verify_off_on_corrupt_command_file(env):
    env.command_file.write_text("{not valid json")
    assert FirefoxAdapter(paths=env.paths, runner=env.runner).verify("off") is True


def test_revert_writes_revert_action(env):
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()
    adapter.revert(snap)
    cmd = json.loads(env.command_file.read_text())
    assert cmd["action"] == "revert"


def test_revert_refuses_an_empty_snapshot(env):
    """The old version reverted on an empty snapshot and left both files in
    place with a comment; apply() writes them, so they are user state the
    adapter must put back — and it refuses when it has no capture."""
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    with pytest.raises(AdapterError, match="refusing"):
        adapter.revert({})


def test_revert_restores_userchrome_and_user_js(env):
    """The round trip: userChrome.css and user.js are captured before apply
    and put back byte-for-byte."""
    user_css = "user chrome — nightpanel must put this back\n"
    user_js_text = 'user_pref("browser.tabs.tabClipWidth", 200);\n'
    (env.profile / "chrome").mkdir(parents=True, exist_ok=True)
    (env.profile / "chrome" / "userChrome.css").write_text(user_css)
    (env.profile / "user.js").write_text(user_js_text)

    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    snap = adapter.snapshot()
    adapter.apply(NIGHTPANEL)
    adapter.revert(snap)

    assert (env.profile / "chrome" / "userChrome.css").read_text() == user_css
    assert (env.profile / "user.js").read_text() == user_js_text


def test_install_always_true(env):
    assert FirefoxAdapter(paths=env.paths, runner=env.runner).installed() is True

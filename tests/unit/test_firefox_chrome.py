# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Firefox userChrome.css surface (the absorbed slimfox layer).

This is the SECOND half of nightpanel's Firefox integration:
  - `services/firefox-extension/background.js` styles page CONTENT  (covered in
    the e2e marionette matrix)
  - `renderers/firefox_chrome.py` + `adapters/firefox.py::_install_user_chrome`
    style Firefox's UI CHROME (this file)

Firefox does not hot-reload userChrome.css — it's read once at app startup.
So unit tests verify the *file produced* is correct; visual verification of
the chrome in a running FF is documented in the test plan but is a separate
manual / xvfb-headed step.

Migrated from monkeypatching module-level path constants to injected
``Paths``/``Runner`` (see tests/conftest.py): the profile is discovered through
the fixture's own profiles.ini instead of by patching ``find_default_profile``,
the brightness lookup is answered by the runner instead of by patching ``_run``,
and revert is exercised with a real snapshot — the old test asserted that revert
left userChrome.css alone, which is no longer the contract.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nightpanel.adapters.base import AdapterError
from nightpanel.adapters.firefox import FirefoxAdapter
from nightpanel.palette import Palette
from nightpanel.paths import Paths
from nightpanel.renderers import firefox_chrome
from nightpanel.runner import RecordingRunner


# Helper: build a Palette without depending on the canonical one (so a future
# palette tweak doesn't silently break these tests).
def _palette(**overrides):
    return Palette(**overrides) if overrides else Palette()


# ── renderer determinism ──────────────────────────────────────────────────


def test_same_palette_same_output() -> None:
    """The renderer must be a pure function of the Palette."""
    a = firefox_chrome.render(_palette())
    b = firefox_chrome.render(_palette())
    assert a == b


def test_palette_value_threads_through() -> None:
    """`p.border_q` from the palette should appear in the rendered CSS."""
    css = firefox_chrome.render(_palette(border_q="#DEADBE"))
    assert "#DEADBE" in css, "border_q palette value did not reach the rendered CSS"


def test_includes_slimfox_collapse_rule() -> None:
    """The absorbed slimfox layer must collapse #navigator-toolbox.

    Without this rule, the chrome is full-size — the slimfox layer is
    the user's whole reason for the userChrome.css existing.
    """
    css = firefox_chrome.render(_palette())
    assert "#navigator-toolbox" in css
    assert "max-height" in css and "0 !important" in css


def test_includes_sharp_corner_rules() -> None:
    """nightpanel design language: 0 border-radius everywhere."""
    css = firefox_chrome.render(_palette())
    # Every radius knob FF exposes
    for var in [
        "--tab-border-radius",
        "--toolbarbutton-border-radius",
        "--arrowpanel-border-radius",
        "--button-border-radius",
        "--urlbar-border-radius",
        "--identity-box-border-radius",
    ]:
        assert var in css, f"expected radius var {var} missing from rendered chrome"


# ── adapter installs userChrome ───────────────────────────────────────────


@pytest.fixture
def env(paths: Paths, runner: RecordingRunner):
    """A Firefox-like profile discovered through the fixture's own profiles.ini.

    Nothing is patched: the adapter resolves its profile from ``paths.home``, so
    pointing `home` at the fixture is enough for the whole tree to land there.
    """
    ff_root = paths.home / ".mozilla" / "firefox"
    profile = ff_root / "abc.default-test"
    profile.mkdir(parents=True)
    (ff_root / "profiles.ini").write_text("[Profile0]\nPath=abc.default-test\nDefault=1\n")

    # Unanswered gsettings calls exit 0 with empty stdout, which is what a
    # running-but-keyless schema looks like; the adapter clamps that to its
    # documented fallbacks. Pinning the answers keeps the assertion honest about
    # where the number came from.
    runner.answers[("gsettings", "get", "io.github.gregfelice.Nightpanel", "theme-brightness")] = (
        "0.6"
    )
    runner.answers[
        ("gsettings", "get", "io.github.gregfelice.Nightpanel", "video-brightness")
    ] = "0.5"
    return SimpleNamespace(
        paths=paths,
        runner=runner,
        command_file=paths.nightpanel / "np-command.json",
        profile=profile,
        user_chrome=profile / "chrome" / "userChrome.css",
        user_js=profile / "user.js",
    )


def test_apply_writes_userchrome_with_palette_content(env) -> None:
    """apply() should write userChrome.css with the renderer's output."""
    palette = _palette()
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(palette)

    assert env.user_chrome.exists(), "userChrome.css was not written"
    assert env.user_chrome.read_text() == firefox_chrome.render(palette)


def test_apply_sets_legacy_stylesheets_pref(env) -> None:
    """apply() must ensure user.js sets toolkit.legacyUserProfileCustomizations.stylesheets=true.

    Without this pref, Firefox ignores userChrome.css entirely.
    """
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(_palette())
    assert 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true)' in (
        env.user_js.read_text()
    )


def test_apply_pref_idempotent(env) -> None:
    """Calling apply() twice does not duplicate the pref line."""
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    adapter.apply(_palette())
    adapter.apply(_palette())
    # Count occurrences of the pref key — must be exactly 1
    assert env.user_js.read_text().count("toolkit.legacyUserProfileCustomizations.stylesheets") == 1


def test_apply_preserves_existing_user_js(env) -> None:
    """An existing user.js line (unrelated to NP) must survive apply()."""
    env.user_js.write_text('user_pref("browser.tabs.tabClipWidth", 200);\n')
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(_palette())

    text = env.user_js.read_text()
    assert "browser.tabs.tabClipWidth" in text
    assert "toolkit.legacyUserProfileCustomizations.stylesheets" in text


def test_apply_writes_command_file(env) -> None:
    """apply() also writes the np-command.json the page-content extension reads.

    This is the OTHER half of the integration — the command file the
    WebExtension's native messaging host polls. The brightness values come from
    the runner's answers, so the assertion names where they came from.
    """
    FirefoxAdapter(paths=env.paths, runner=env.runner).apply(_palette())

    cmd = json.loads(env.command_file.read_text())
    assert cmd["action"] == "apply"
    assert cmd["brightness"] == 0.6
    assert cmd["videoBrightness"] == 0.5
    theme_call = ["gsettings", "get", "io.github.gregfelice.Nightpanel", "theme-brightness"]
    assert theme_call in env.runner.calls, "brightness was read without going through the Runner"


def test_revert_restores_chrome_and_writes_command_file(env) -> None:
    """revert() puts userChrome.css and user.js back byte-for-byte and tells the
    page-content extension to stop applying CSS.

    The old version left userChrome.css in place on the reasoning that chrome
    styling "is not a per-toggle concern" — but apply() writes both files, so
    they are user state this adapter mutates and must put back. Firefox still
    needs a restart to pick the change up; that is a UX note, not a licence to
    skip the restore.
    """
    existing_css = "user chrome written before nightpanel — must survive\n"
    existing_js = 'user_pref("browser.tabs.tabClipWidth", 200);\n'
    (env.profile / "chrome").mkdir(parents=True, exist_ok=True)
    env.user_chrome.write_text(existing_css)
    env.user_js.write_text(existing_js)

    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    snapshot = adapter.snapshot()
    adapter.apply(_palette())
    assert env.user_chrome.read_text() != existing_css, "apply did not restyle the chrome"

    adapter.revert(snapshot)

    assert env.user_chrome.read_text() == existing_css
    assert env.user_js.read_text() == existing_js
    assert json.loads(env.command_file.read_text())["action"] == "revert"


def test_revert_refuses_an_empty_snapshot(env) -> None:
    """A revert with no capture is a guess about whether the files existed."""
    with pytest.raises(AdapterError, match="refusing"):
        FirefoxAdapter(paths=env.paths, runner=env.runner).revert({})


def test_revert_removes_files_that_did_not_exist_before_apply(env) -> None:
    """A fresh profile has no userChrome.css before apply; revert must remove it
    rather than leave nightpanel's rendering behind as the user's chrome."""
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    snapshot = adapter.snapshot()
    adapter.apply(_palette())
    adapter.revert(snapshot)

    assert not env.user_chrome.exists(), "apply wrote chrome that revert should have removed"
    assert not env.user_js.exists(), "apply wrote user.js that revert should have removed"


def test_apply_without_a_profile_is_not_a_failure(env) -> None:
    """No discoverable profile is an answer, not a guess: the command file is
    still written and the chrome half is skipped with a log line."""
    (env.paths.home / ".mozilla" / "firefox" / "profiles.ini").unlink()
    adapter = FirefoxAdapter(paths=env.paths, runner=env.runner)
    adapter.apply(_palette())

    assert json.loads(env.command_file.read_text())["action"] == "apply"
    assert not env.user_chrome.exists()

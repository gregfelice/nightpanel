# SPDX-License-Identifier: GPL-3.0-or-later
"""Every symbolic icon name the UI asks for exists in Adwaita.

A name that is not in the theme renders as a missing-image placeholder, and
nothing else fails: `emblem-ok-symbolic` shipped that way in two views.
"""

import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi", reason="GTK bindings required")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src" / "nightpanel"
NAME = re.compile(r"""["']([a-z][a-z0-9-]*-symbolic)["']|>([a-z][a-z0-9-]*-symbolic)<""")


def _adwaita() -> Gtk.IconTheme:
    # Ask GTK, not the filesystem: it also resolves aliases and the icons GTK itself bundles.
    theme = Gtk.IconTheme()
    theme.set_theme_name("Adwaita")
    if not theme.has_icon("open-menu-symbolic"):
        pytest.skip("Adwaita icon theme not installed")
    return theme


def _used() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for p in [*SRC.rglob("*.py"), *SRC.rglob("*.ui")]:
        for m in NAME.finditer(p.read_text()):
            used.setdefault(m.group(1) or m.group(2), []).append(p.name)
    return used


def test_every_symbolic_icon_exists_in_adwaita():
    theme = _adwaita()
    missing = {n: sorted(set(f)) for n, f in _used().items() if not theme.has_icon(n)}
    assert not missing, f"icon names absent from Adwaita: {missing}"


def test_the_scan_finds_icons():
    assert len(_used()) > 15

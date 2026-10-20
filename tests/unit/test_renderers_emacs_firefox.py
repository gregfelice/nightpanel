# SPDX-License-Identifier: GPL-3.0-or-later
"""The emacs theme and firefox selection renderers take every color from the Palette."""

from dataclasses import replace

from nightpanel.palette import NIGHTPANEL
from nightpanel.renderers import emacs, firefox

# A palette whose values differ from the default, so a hardcoded hex cannot pass.
OTHER = replace(NIGHTPANEL, bg="#102030", fg="#a0b0c0", bg_select="#334455", fg_bright="#ddeeff")


def test_emacs_theme_uses_the_palette_instance():
    out = emacs.render(OTHER)
    assert "(deftheme nightpanel" in out
    assert "(provide-theme 'nightpanel)" in out
    assert OTHER.bg in out and OTHER.fg in out


def test_emacs_theme_has_no_unrendered_placeholders():
    out = emacs.render(NIGHTPANEL)
    assert "{p." not in out and "{{" not in out


def test_firefox_selection_css_uses_the_palette_instance():
    out = firefox.render_selection_css(OTHER)
    assert OTHER.bg_select in out and OTHER.fg_bright in out
    assert out.startswith("::selection")

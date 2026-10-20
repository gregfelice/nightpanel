# SPDX-License-Identifier: GPL-3.0-or-later
"""Every module under src/nightpanel must appear in a meson install list.

The battery polices the adapters: a new adapter that is not registered fails ./bin/test. The
same shape of miss sits one level up, and nothing policed it. `330d2f0` added
src/nightpanel/paths.py and src/nightpanel/runner.py; the source tree imported fine, so the whole
suite passed, and the installed .deb failed at `from ..paths import Paths`. That failure surfaces
only in CI's proof job on v* tags — the last place a packaging gap should be found.

Entries are resolved the way meson resolves them: relative to the directory holding the
meson.build that lists them. The guard is a check on the lists, not a meson parser — it cannot
tell an entry that is listed from an entry that is actually installed, so it is a guard against
omission, not a proof of the install.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"


def _installed_paths(root: Path) -> set[str]:
    """Every .py entry in every meson.build under src/, resolved to its source path."""
    installed: set[str] = set()
    for build in (root / "src").rglob("meson.build"):
        for entry in re.findall(r"'([^']+\.py)'", build.read_text()):
            installed.add(str((build.parent / entry).relative_to(root / "src")))
    return installed


def _unlisted_modules(root: Path) -> list[str]:
    """Modules on disk that no install list names."""
    installed = _installed_paths(root)
    missing = []
    for module in (root / "src" / "nightpanel").rglob("*.py"):
        if "__pycache__" in module.parts:
            continue
        rel = str(module.relative_to(root / "src"))
        if rel not in installed:
            missing.append(rel)
    return sorted(missing)


class TestInstallList:
    """The guard, and the proof that it bites."""

    def test_every_module_is_in_an_install_list(self) -> None:
        missing = _unlisted_modules(REPO)
        assert not missing, f"modules on disk that no meson install list names: {missing}"

    def test_a_module_written_and_never_listed_is_reported(self, tmp_path: Path) -> None:
        """The miss this guard exists for, on the shape of the miss."""
        package = tmp_path / "src" / "nightpanel"
        package.mkdir(parents=True)
        (package / "listed.py").write_text("")
        (package / "forgotten.py").write_text("")
        (tmp_path / "src" / "meson.build").write_text(
            "install_data(['nightpanel/listed.py'], install_dir: moduledir)\n"
        )
        assert _unlisted_modules(tmp_path) == ["nightpanel/forgotten.py"]

    def test_a_subpackage_module_is_covered_by_its_own_install_list(self, tmp_path: Path) -> None:
        """An entry is relative to the meson.build that lists it, so a subpackage names its own."""
        package = tmp_path / "src" / "nightpanel"
        sub = package / "adapters"
        sub.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (sub / "__init__.py").write_text("")
        (tmp_path / "src" / "meson.build").write_text(
            "install_data(['nightpanel/__init__.py'], install_dir: moduledir)\n"
            "subdir('nightpanel/adapters')\n"
        )
        (sub / "meson.build").write_text(
            "install_data(['__init__.py'], install_dir: moduledir / 'adapters')\n"
        )
        assert _unlisted_modules(tmp_path) == []

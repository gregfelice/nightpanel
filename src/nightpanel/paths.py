# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths — the resolved filesystem locations adapters write to.

Resolved once and injected. Module-level ``Path.home()`` constants were the
root cause of two classes of bug:

1. Tests could only be hermetic by monkey-patching module globals, which made
   every adapter test depend on the developer's real home directory the moment
   a fixture was forgotten.
2. Any machine whose config lives outside the default — ``XDG_CONFIG_HOME``,
   ``XDG_STATE_HOME``, a non-default dotfiles layout — was wrong by
   construction, because the constants were bound at import time.

``Paths.detect()`` is the production entry point. Tests construct
``Paths(home=tmp_path)`` and every adapter's files land under the fixture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _first_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


@dataclass(frozen=True)
class Paths:
    """A resolved set of user-writable locations.

    Every field is derived from ``home`` unless given explicitly, so a test can
    point the whole tree at a tmpdir with one argument.
    """

    home: Path = field(default_factory=Path.home)
    config: Path | None = None
    data: Path | None = None
    state: Path | None = None
    bin: Path | None = None
    runtime: Path | None = None
    nightpanel: Path | None = None

    def __post_init__(self) -> None:
        home = Path(self.home)
        object.__setattr__(self, "home", home)
        object.__setattr__(self, "config", Path(self.config) if self.config else home / ".config")
        object.__setattr__(
            self, "data", Path(self.data) if self.data else home / ".local" / "share"
        )
        object.__setattr__(
            self, "state", Path(self.state) if self.state else home / ".local" / "state"
        )
        object.__setattr__(self, "bin", Path(self.bin) if self.bin else home / ".local" / "bin")
        # Derived from home so a test that points `home` at a tmpdir also
        # captures the runtime dir; `detect()` passes the real XDG_RUNTIME_DIR.
        object.__setattr__(
            self, "runtime", Path(self.runtime) if self.runtime else home / ".runtime"
        )
        object.__setattr__(
            self,
            "nightpanel",
            Path(self.nightpanel) if self.nightpanel else self.config / "nightpanel",
        )

    @classmethod
    def detect(cls) -> Paths:
        """Resolve from the environment, honouring the XDG base directories."""
        home = Path.home()
        config = _first_env("XDG_CONFIG_HOME", home / ".config")
        data = _first_env("XDG_DATA_HOME", home / ".local" / "share")
        state = _first_env("XDG_STATE_HOME", home / ".local" / "state")
        bin_ = _first_env("XDG_BIN_HOME", home / ".local" / "bin")
        runtime = _first_env("XDG_RUNTIME_DIR", Path(f"/run/user/{os.getuid()}"))
        # NP_CONFIG_DIR is the documented override the toggle script and the
        # GNOME adapter already honour; keep it working.
        nightpanel = _first_env("NP_CONFIG_DIR", config / "nightpanel")
        return cls(
            home=home,
            config=config,
            data=data,
            state=state,
            bin=bin_,
            runtime=runtime,
            nightpanel=nightpanel,
        )

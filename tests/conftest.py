# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test fixtures for DailyDriver tests."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nightpanel.paths import Paths
from nightpanel.runner import RecordingRunner

# =============================================================================
# Privilege-escalation guardrail (autouse — applies to every test)
# =============================================================================
#
# If a unit test reaches production code that shells out to `pkexec`/`sudo`/
# `doas`, the real binary runs and prompts the developer for a password.
# This has happened (see test_apply_modifier_config_success in
# test_keyboard_config.py: it mocks Gio but the code path also instantiates
# HidAppleService, which runs `pkexec sh -c "modprobe -r hid_apple ..."` in
# apply_config). The right move is to make the test harness *incapable* of
# triggering a real elevation prompt — not to play whack-a-mole patching
# each new test as it pops up.
#
# The guard wraps the five subprocess entry points (`run`, `Popen`, `call`,
# `check_call`, `check_output`). When the first command token resolves to a
# known privilege-escalator, the wrapper raises a noisy RuntimeError instead
# of executing. Everything else (modinfo, the nightpanel-toggle integration
# script, etc.) passes through unchanged.
#
# Tests that legitimately need to exercise those paths can override the
# wrapper by doing their own `patch("subprocess.run", ...)` — the test-level
# patch wins because it applies inside the test body, after this autouse
# fixture has installed the guard.

_PRIVILEGE_ESCALATORS = frozenset({"pkexec", "sudo", "doas"})


def _command_looks_privileged(args: object) -> bool:
    """Return True if the subprocess command's argv[0] is a known elevator."""
    if isinstance(args, (str, bytes)):
        # shell=True string form — pull off the first token.
        text = args.decode("ascii", "replace") if isinstance(args, bytes) else args
        tokens = text.split()
        first = tokens[0] if tokens else ""
    elif isinstance(args, (list, tuple)) and args:
        first = args[0]
        if isinstance(first, bytes):
            first = first.decode("ascii", "replace")
        first = str(first)
    else:
        return False
    # Match both "pkexec" and "/usr/bin/pkexec".
    return Path(first).name in _PRIVILEGE_ESCALATORS


@pytest.fixture(autouse=True)
def _block_privileged_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Refuse any subprocess call that would prompt for elevated privileges."""

    def _make_guard(real: Any, name: str) -> Any:
        def wrapped(args: Any, *rest: Any, **kw: Any) -> Any:
            if _command_looks_privileged(args):
                raise RuntimeError(
                    f"nightpanel test guard: refused subprocess.{name}({args!r}). "
                    "A test reached production code that invokes pkexec/sudo/doas. "
                    "Mock the offending call (e.g. patch('subprocess.run') or "
                    "patch the service object that owns the shell-out) rather "
                    "than letting the real elevator run."
                )
            return real(args, *rest, **kw)

        return wrapped

    for fn_name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(
            subprocess,
            fn_name,
            _make_guard(getattr(subprocess, fn_name), fn_name),
        )
    yield


# =============================================================================
# Early GI Mocking (before any dailydriver imports)
# =============================================================================

# Keyval lookup tables (defined early for mock functions)
_KEYVAL_NAMES = {
    # Letters
    0x61: "a",
    0x62: "b",
    0x63: "c",
    0x64: "d",
    0x65: "e",
    0x66: "f",
    0x67: "g",
    0x68: "h",
    0x69: "i",
    0x6A: "j",
    0x6B: "k",
    0x6C: "l",
    0x6D: "m",
    0x6E: "n",
    0x6F: "o",
    0x70: "p",
    0x71: "q",
    0x72: "r",
    0x73: "s",
    0x74: "t",
    0x75: "u",
    0x76: "v",
    0x77: "w",
    0x78: "x",
    0x79: "y",
    0x7A: "z",
    # Numbers
    0x30: "0",
    0x31: "1",
    0x32: "2",
    0x33: "3",
    0x34: "4",
    0x35: "5",
    0x36: "6",
    0x37: "7",
    0x38: "8",
    0x39: "9",
    # Special keys
    0xFF09: "Tab",
    0xFF0D: "Return",
    0xFF1B: "Escape",
    0x20: "space",
    0xFF51: "Left",
    0xFF52: "Up",
    0xFF53: "Right",
    0xFF54: "Down",
    0xFF55: "Page_Up",
    0xFF56: "Page_Down",
    0xFF50: "Home",
    0xFF57: "End",
    0xFF08: "BackSpace",
    0xFFFF: "Delete",
    0xFF63: "Insert",
    # Function keys
    0xFFBE: "F1",
    0xFFBF: "F2",
    0xFFC0: "F3",
    0xFFC1: "F4",
    0xFFC2: "F5",
    0xFFC3: "F6",
    0xFFC4: "F7",
    0xFFC5: "F8",
    0xFFC6: "F9",
    0xFFC7: "F10",
    0xFFC8: "F11",
    0xFFC9: "F12",
    # Symbols
    0x60: "grave",
    0x2F: "slash",
    0x2C: "comma",
    0x2E: "period",
    0x2D: "minus",
    0x3D: "equal",
    0x5B: "bracketleft",
    0x5D: "bracketright",
    0x5C: "backslash",
    0x3B: "semicolon",
    0x27: "apostrophe",
    # Keypad keys
    0xFFB0: "KP_0",
    0xFFB1: "KP_1",
    0xFFB2: "KP_2",
    0xFFB3: "KP_3",
    0xFFB4: "KP_4",
    0xFFB5: "KP_5",
    0xFFB6: "KP_6",
    0xFFB7: "KP_7",
    0xFFB8: "KP_8",
    0xFFB9: "KP_9",
    0xFFAE: "KP_Decimal",
    0xFFAB: "KP_Add",
    0xFFAD: "KP_Subtract",
    0xFFAA: "KP_Multiply",
    0xFFAF: "KP_Divide",
    0xFF8D: "KP_Enter",
    # XF86 media keys
    0x1008FF14: "XF86AudioPlay",
    0x1008FF31: "XF86AudioPause",
    0x1008FF15: "XF86AudioStop",
    0x1008FF16: "XF86AudioPrev",
    0x1008FF17: "XF86AudioNext",
    0x1008FF13: "XF86AudioRaiseVolume",
    0x1008FF11: "XF86AudioLowerVolume",
    0x1008FF12: "XF86AudioMute",
    0x1008FF02: "XF86MonBrightnessUp",
    0x1008FF03: "XF86MonBrightnessDown",
    0x1008FF59: "XF86Display",
    0x1008FF2D: "XF86PowerOff",
    0x1008FF41: "XF86Launch1",
    0x1008FF45: "XF86Launch5",
}

_KEYVAL_FROM_NAME = {v: k for k, v in _KEYVAL_NAMES.items()}


def _mock_accelerator_parse(accelerator: str) -> tuple[bool, int, int]:
    """Parse a GTK accelerator string."""
    if not accelerator or accelerator == "disabled":
        return False, 0, 0

    mods = 0
    key_part = accelerator

    while key_part.startswith("<"):
        end = key_part.find(">")
        if end == -1:
            return False, 0, 0

        mod = key_part[1:end].lower()
        key_part = key_part[end + 1 :]

        if mod in ("super", "mod4"):
            mods |= 0x04000000
        elif mod in ("ctrl", "control"):
            mods |= 4
        elif mod in ("alt", "mod1"):
            mods |= 8
        elif mod == "shift":
            mods |= 1
        elif mod == "hyper":
            mods |= 0x08000000
        elif mod == "meta":
            mods |= 0x10000000

    keyval = _KEYVAL_FROM_NAME.get(key_part, 0)
    if keyval == 0:
        return False, 0, 0

    return True, keyval, mods


def _mock_accelerator_name(keyval: int, mods: int) -> str:
    """Convert keyval and modifiers to accelerator string."""
    parts = []

    if mods & 1:
        parts.append("<Shift>")
    if mods & 4:
        parts.append("<Control>")
    if mods & 8:
        parts.append("<Alt>")
    if mods & 0x04000000:
        parts.append("<Super>")
    if mods & 0x08000000:
        parts.append("<Hyper>")
    if mods & 0x10000000:
        parts.append("<Meta>")

    key_name = _KEYVAL_NAMES.get(keyval)
    if key_name:
        parts.append(key_name)

    return "".join(parts)


def _mock_accelerator_get_label(keyval: int, mods: int) -> str:
    """Convert keyval and modifiers to human-readable label."""
    parts = []

    if mods & 0x04000000:
        parts.append("Super")
    if mods & 4:
        parts.append("Ctrl")
    if mods & 8:
        parts.append("Alt")
    if mods & 1:
        parts.append("Shift")

    key_name = _KEYVAL_NAMES.get(keyval)
    if key_name:
        humanized = {
            "Return": "Enter",
            "Escape": "Esc",
            "grave": "`",
            "space": "Space",
            "comma": ",",
            "period": ".",
            "slash": "/",
        }
        parts.append(humanized.get(key_name, key_name.upper() if len(key_name) == 1 else key_name))

    return "+".join(parts)


class _EarlyMockGdkModifierType(int):
    """Early mock for Gdk.ModifierType before full fixtures are loaded."""

    SHIFT_MASK = 1
    CONTROL_MASK = 4
    ALT_MASK = 8
    SUPER_MASK = 0x04000000
    HYPER_MASK = 0x08000000
    META_MASK = 0x10000000

    def __new__(cls, value: int = 0) -> _EarlyMockGdkModifierType:
        return super().__new__(cls, value)

    def __or__(self, other: int) -> _EarlyMockGdkModifierType:
        return _EarlyMockGdkModifierType(int(self) | int(other))

    def __and__(self, other: int) -> int:
        return int(self) & int(other)


def _setup_gi_mocks() -> None:
    """Set up mock gi module and submodules."""
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()

    # Create mock Gdk
    mock_gdk = MagicMock()
    mock_gdk.ModifierType = _EarlyMockGdkModifierType
    mock_gdk.keyval_name = lambda k: _KEYVAL_NAMES.get(k)
    mock_gdk.keyval_from_name = lambda n: _KEYVAL_FROM_NAME.get(n, 0)

    # Create mock Gtk
    mock_gtk = MagicMock()
    mock_gtk.accelerator_parse = _mock_accelerator_parse
    mock_gtk.accelerator_name = _mock_accelerator_name
    mock_gtk.accelerator_get_label = _mock_accelerator_get_label

    # Create mock Gio
    mock_gio = MagicMock()

    # Create mock GLib
    mock_glib = MagicMock()
    mock_glib.get_user_config_dir = lambda: "/tmp/nightpanel-test/config"
    mock_glib.get_system_data_dirs = lambda: ["/usr/share", "/usr/local/share"]

    # Create mock repository
    mock_repository = MagicMock()
    mock_repository.Gdk = mock_gdk
    mock_repository.Gtk = mock_gtk
    mock_repository.Gio = mock_gio
    mock_repository.GLib = mock_glib

    mock_gi.repository = mock_repository

    sys.modules["gi"] = mock_gi
    sys.modules["gi.repository"] = mock_repository


# Check if gi is available and set up mocks if needed
try:
    import gi  # noqa: F401

    HAS_GI = True
except ImportError:
    HAS_GI = False
    # Set up mock gi module for environments without GTK
    _setup_gi_mocks()


# =============================================================================
# GTK/GLib Mocking Infrastructure (for explicit fixture use)
# =============================================================================


class MockGdkModifierType(int):
    """Mock Gdk.ModifierType for tests.

    Inherits from int to support bitwise operations and constructor with int argument.
    """

    SHIFT_MASK = 1
    CONTROL_MASK = 4
    ALT_MASK = 8
    SUPER_MASK = 0x04000000
    HYPER_MASK = 0x08000000
    META_MASK = 0x10000000

    def __new__(cls, value: int = 0) -> MockGdkModifierType:
        """Create a new modifier type with the given value."""
        return super().__new__(cls, value)

    def __or__(self, other: int) -> MockGdkModifierType:
        """Support bitwise OR operation."""
        return MockGdkModifierType(int(self) | int(other))

    def __and__(self, other: int) -> int:
        """Support bitwise AND operation."""
        return int(self) & int(other)


class MockGdk:
    """Mock Gdk module for tests."""

    ModifierType = MockGdkModifierType

    # Common keyvals
    KEY_a = 0x61
    KEY_b = 0x62
    KEY_c = 0x63
    KEY_e = 0x65
    KEY_h = 0x68
    KEY_l = 0x6C
    KEY_p = 0x70
    KEY_v = 0x76
    KEY_1 = 0x31
    KEY_2 = 0x32
    KEY_3 = 0x33
    KEY_4 = 0x34
    KEY_Tab = 0xFF09
    KEY_Return = 0xFF0D
    KEY_Escape = 0xFF1B
    KEY_space = 0x20
    KEY_Left = 0xFF51
    KEY_Up = 0xFF52
    KEY_Right = 0xFF53
    KEY_Down = 0xFF54
    KEY_Page_Up = 0xFF55
    KEY_Page_Down = 0xFF56
    KEY_F4 = 0xFFC1
    KEY_grave = 0x60
    KEY_slash = 0x2F
    KEY_comma = 0x2C
    KEY_period = 0x2E

    # XF86 media keys
    KEY_XF86AudioPlay = 0x1008FF14
    KEY_XF86AudioPause = 0x1008FF31
    KEY_XF86AudioStop = 0x1008FF15
    KEY_XF86AudioPrev = 0x1008FF16
    KEY_XF86AudioNext = 0x1008FF17
    KEY_XF86AudioRaiseVolume = 0x1008FF13
    KEY_XF86AudioLowerVolume = 0x1008FF11
    KEY_XF86AudioMute = 0x1008FF12

    KEYVAL_NAMES = _KEYVAL_NAMES
    KEYVAL_FROM_NAME = _KEYVAL_FROM_NAME

    @staticmethod
    def keyval_name(keyval: int) -> str | None:
        """Mock keyval_name."""
        return _KEYVAL_NAMES.get(keyval)

    @staticmethod
    def keyval_from_name(name: str) -> int:
        """Mock keyval_from_name."""
        return _KEYVAL_FROM_NAME.get(name, 0)


class MockGtk:
    """Mock Gtk module for tests."""

    accelerator_parse = staticmethod(_mock_accelerator_parse)
    accelerator_name = staticmethod(_mock_accelerator_name)
    accelerator_get_label = staticmethod(_mock_accelerator_get_label)


class MockGio:
    """Mock Gio module for tests."""

    class Settings:
        """Mock Gio.Settings for tests."""

        def __init__(self, schema_id: str, path: str | None = None) -> None:
            self.schema_id = schema_id
            self.path = path
            self._data: dict[str, Any] = {}

        def get_strv(self, key: str) -> list[str]:
            return self._data.get(key, [])

        def set_strv(self, key: str, value: list[str]) -> None:
            self._data[key] = value

        def get_string(self, key: str) -> str:
            return self._data.get(key, "")

        def set_string(self, key: str, value: str) -> None:
            self._data[key] = value

        def get_value(self, key: str) -> Any:
            return self._data.get(key)

        def set_value(self, key: str, value: Any) -> None:
            self._data[key] = value

        def reset(self, key: str) -> None:
            if key in self._data:
                del self._data[key]

    class SettingsSchema:
        """Mock Gio.SettingsSchema for tests."""

        def __init__(self, schema_id: str, keys: dict[str, dict[str, Any]] | None = None) -> None:
            self.schema_id = schema_id
            self._keys = keys or {}

        def list_keys(self) -> list[str]:
            return list(self._keys.keys())

        def get_key(self, key: str) -> Any:
            if key not in self._keys:
                return None
            return MockGio.SettingsSchemaKey(self._keys[key])

    class SettingsSchemaKey:
        """Mock Gio.SettingsSchemaKey for tests."""

        def __init__(self, key_data: dict[str, Any]) -> None:
            self._data = key_data

        def get_value_type(self) -> Any:
            return MockGio.VariantType(self._data.get("type", "as"))

        def get_default_value(self) -> Any:
            return self._data.get("default")

        def get_description(self) -> str:
            return self._data.get("description", "")

    class SettingsSchemaSource:
        """Mock Gio.SettingsSchemaSource for tests."""

        _schemas: dict[str, MockGio.SettingsSchema] = {}

        @classmethod
        def get_default(cls) -> MockGio.SettingsSchemaSource:
            return cls()

        def lookup(self, schema_id: str, recursive: bool) -> MockGio.SettingsSchema | None:
            return self._schemas.get(schema_id)

        @classmethod
        def register_schema(cls, schema: MockGio.SettingsSchema) -> None:
            cls._schemas[schema.schema_id] = schema

        @classmethod
        def clear_schemas(cls) -> None:
            cls._schemas.clear()

    class VariantType:
        """Mock GLib.VariantType for tests."""

        def __init__(self, type_string: str) -> None:
            self._type_string = type_string

        def dup_string(self) -> str:
            return self._type_string

    @staticmethod
    def Settings_new(schema_id: str) -> MockGio.Settings:
        return MockGio.Settings(schema_id)

    @staticmethod
    def Settings_new_with_path(schema_id: str, path: str) -> MockGio.Settings:
        return MockGio.Settings(schema_id, path)


class MockGLib:
    """Mock GLib module for tests."""

    class Variant:
        """Mock GLib.Variant for tests."""

        def __init__(self, type_string: str, value: Any) -> None:
            self._type_string = type_string
            self._value = value

        def get_type_string(self) -> str:
            return self._type_string

        def unpack(self) -> Any:
            return self._value

    @staticmethod
    def get_user_config_dir() -> str:
        """Return mock config directory."""
        return "/tmp/nightpanel-test/config"

    @staticmethod
    def get_system_data_dirs() -> list[str]:
        """Return mock system data directories."""
        return ["/usr/share", "/usr/local/share"]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_gi() -> Generator[dict[str, Any], None, None]:
    """Mock the gi module and its submodules for tests without GTK installed."""
    mock_gi_module = MagicMock()
    mock_gi_module.require_version = MagicMock()

    # Create mock repository
    mock_repository = MagicMock()
    mock_repository.Gdk = MockGdk
    mock_repository.Gtk = MockGtk
    mock_repository.Gio = MockGio
    mock_repository.GLib = MockGLib

    mock_gi_module.repository = mock_repository

    with patch.dict(
        sys.modules,
        {
            "gi": mock_gi_module,
            "gi.repository": mock_repository,
        },
    ):
        yield {
            "gi": mock_gi_module,
            "Gdk": MockGdk,
            "Gtk": MockGtk,
            "Gio": MockGio,
            "GLib": MockGLib,
        }


@pytest.fixture
def mock_gsettings() -> Generator[dict[str, MockGio.Settings], None, None]:
    """Provide a mock GSettings backend with in-memory storage."""
    settings_instances: dict[str, MockGio.Settings] = {}

    def create_settings(schema_id: str) -> MockGio.Settings:
        if schema_id not in settings_instances:
            settings_instances[schema_id] = MockGio.Settings(schema_id)
        return settings_instances[schema_id]

    with patch.object(MockGio, "Settings_new", side_effect=create_settings):
        yield settings_instances


@pytest.fixture
def mock_sysfs(tmp_path: Path) -> Generator[Path, None, None]:
    """Mock /sys/ filesystem for hardware detection tests.

    Creates a minimal sysfs structure for keyboard detection.
    """
    sys_path = tmp_path / "sys"
    input_path = sys_path / "class" / "input"
    input_path.mkdir(parents=True)

    yield sys_path


def create_mock_keyboard(
    sys_path: Path,
    event_num: int,
    name: str,
    vendor_id: int = 0x0001,
    product_id: int = 0x0001,
    # Default: enough bits for a real keyboard (50+ key bits set)
    key_capabilities: str = "fffffffe ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff 0 0 0 0 0 0 0 0",
    is_bluetooth: bool = False,
) -> Path:
    """Create a mock keyboard device in sysfs.

    Args:
        sys_path: Base mock sysfs path
        event_num: Event number (e.g., 0 for event0)
        name: Device name
        vendor_id: USB vendor ID
        product_id: USB product ID
        key_capabilities: Key capability bits (hex string)
        is_bluetooth: Whether this is a Bluetooth device

    Returns:
        Path to the created event directory
    """
    input_path = sys_path / "class" / "input"
    event_dir = input_path / f"event{event_num}"
    device_dir = event_dir / "device"
    device_dir.mkdir(parents=True)

    # Device name
    (device_dir / "name").write_text(f"{name}\n")

    # Vendor/product IDs
    id_dir = device_dir / "id"
    id_dir.mkdir()
    (id_dir / "vendor").write_text(f"{vendor_id:04x}\n")
    (id_dir / "product").write_text(f"{product_id:04x}\n")

    # Key capabilities
    caps_dir = device_dir / "capabilities"
    caps_dir.mkdir()
    (caps_dir / "key").write_text(f"{key_capabilities}\n")

    # Uevent for Bluetooth detection
    if is_bluetooth:
        (device_dir / "uevent").write_text("DRIVER=bluetooth\n")

    return event_dir


@pytest.fixture
def mock_apple_keyboard(mock_sysfs: Path) -> Path:
    """Create a mock Apple Magic Keyboard in sysfs."""
    return create_mock_keyboard(
        mock_sysfs,
        event_num=0,
        name="Apple Inc. Magic Keyboard",
        vendor_id=0x05AC,
        product_id=0x0267,
    )


@pytest.fixture
def mock_generic_keyboard(mock_sysfs: Path) -> Path:
    """Create a mock generic USB keyboard in sysfs."""
    return create_mock_keyboard(
        mock_sysfs,
        event_num=1,
        name="USB Keyboard",
        vendor_id=0x04D9,
        product_id=0x0001,
    )


@pytest.fixture
def mock_hid_apple(tmp_path: Path) -> Generator[Path, None, None]:
    """Mock /sys/module/hid_apple/parameters/ for hid-apple tests."""
    params_path = tmp_path / "sys" / "module" / "hid_apple" / "parameters"
    params_path.mkdir(parents=True)

    # Default values
    (params_path / "fnmode").write_text("2\n")  # Media keys by default
    (params_path / "swap_opt_cmd").write_text("N\n")
    (params_path / "swap_fn_leftctrl").write_text("N\n")
    (params_path / "iso_layout").write_text("N\n")

    yield params_path


@pytest.fixture
def sample_profile_data() -> dict[str, Any]:
    """Return sample profile data for testing."""
    return {
        "profile": {
            "name": "test-profile",
            "description": "Test profile for unit tests",
            "author": "Test",
            "version": "1.0",
            "created": "2024-01-01T00:00:00",
            "modified": "2024-01-01T00:00:00",
        },
        "shortcuts": {
            "org.gnome.desktop.wm.keybindings.close": ["<Alt>F4"],
            "org.gnome.desktop.wm.keybindings.maximize": ["<Super>Up"],
            "org.gnome.mutter.keybindings.toggle-tiled-left": ["<Super>Left"],
        },
        "xkb": {
            "caps_lock": "caps:escape",
        },
    }


@pytest.fixture
def sample_profile_toml(tmp_path: Path, sample_profile_data: dict[str, Any]) -> Path:
    """Create a sample profile TOML file for testing."""
    import tomli_w

    profile_path = tmp_path / "test-profile.toml"
    with open(profile_path, "wb") as f:
        tomli_w.dump(sample_profile_data, f)

    return profile_path


@pytest.fixture
def presets_dir() -> Path:
    """Return the path to the built-in presets directory."""
    return Path(__file__).parent.parent / "src" / "nightpanel" / "resources" / "presets"


# =============================================================================
# Hermetic world for adapter tests
# =============================================================================
#
# The old pattern was `monkeypatch.setattr(mod, "_CFG", tmp_path / ...)`, which
# only worked because the adapter bound its paths at import time — the bug class
# `Paths` exists to remove. With injection there is nothing to patch: point
# `Paths(home=tmp_path)` at the fixture and every file the adapter touches lands
# there. `runner` is the fake live session.
#
# See tests/invariants/conftest.py for the same fixtures in the contract battery.


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    """A hermetic Paths rooted at the fixture. Nothing here touches the real home."""
    return Paths(home=tmp_path)


@pytest.fixture
def runner() -> RecordingRunner:
    """A fake live session: records every command, answers nothing, exits 0."""
    return RecordingRunner()

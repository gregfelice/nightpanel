# SPDX-License-Identifier: GPL-3.0-or-later
"""GNOME shortcuts backend using GSettings/dconf."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

from gi.repository import Gio, GLib

from nightpanel.models import KeyBinding, Shortcut, ShortcutCategory
from nightpanel.paths import Paths
from nightpanel.services.backends.base import ShortcutsBackend

# Known GSettings schemas containing shortcuts
SHORTCUT_SCHEMAS = [
    # Window Manager
    {
        "schema": "org.gnome.desktop.wm.keybindings",
        "category": "window-management",
    },
    # Shell
    {
        "schema": "org.gnome.shell.keybindings",
        "category": "shell",
    },
    # Media Keys
    {
        "schema": "org.gnome.settings-daemon.plugins.media-keys",
        "category": "media",
    },
    # Mutter
    {
        "schema": "org.gnome.mutter.keybindings",
        "category": "window-management",
    },
    # Mutter Wayland
    {
        "schema": "org.gnome.mutter.wayland.keybindings",
        "category": "window-management",
    },
    # Tiling Assistant extension (recommended tiling solution)
    {
        "schema": "org.gnome.shell.extensions.tiling-assistant",
        "category": "tiling",
    },
]

# Category definitions
CATEGORIES = [
    ShortcutCategory(
        id="tiling",
        name="Tiling",
        icon="view-grid-symbolic",
        description="Snap and tile windows",
    ),
    ShortcutCategory(
        id="window-management",
        name="Window Management",
        icon="focus-windows-symbolic",
        description="Move, resize, and manage windows",
    ),
    ShortcutCategory(
        id="navigation",
        name="Navigation",
        icon="go-home-symbolic",
        description="Navigate between workspaces and windows",
    ),
    ShortcutCategory(
        id="shell",
        name="Shell",
        icon="view-app-grid-symbolic",
        description="GNOME Shell functions",
    ),
    ShortcutCategory(
        id="media",
        name="Media",
        icon="multimedia-player-symbolic",
        description="Media playback and volume controls",
    ),
    ShortcutCategory(
        id="accessibility",
        name="Accessibility",
        icon="preferences-desktop-accessibility-symbolic",
        description="Accessibility features",
    ),
    ShortcutCategory(
        id="system",
        name="System",
        icon="preferences-system-symbolic",
        description="System functions like lock screen and power",
    ),
    ShortcutCategory(
        id="custom",
        name="Custom",
        icon="application-x-addon-symbolic",
        description="User-defined shortcuts",
    ),
]

# Key name to category mapping
KEY_CATEGORIES = {
    # Window management
    "close": "window-management",
    "minimize": "window-management",
    "maximize": "window-management",
    "maximize-horizontally": "window-management",
    "maximize-vertically": "window-management",
    "unmaximize": "window-management",
    "toggle-maximized": "window-management",
    "toggle-fullscreen": "window-management",
    "always-on-top": "window-management",
    "toggle-above": "window-management",
    "raise": "window-management",
    "lower": "window-management",
    "move-to-center": "window-management",
    "move-to-corner-nw": "window-management",
    "move-to-corner-ne": "window-management",
    "move-to-corner-sw": "window-management",
    "move-to-corner-se": "window-management",
    "move-to-side-n": "window-management",
    "move-to-side-s": "window-management",
    "move-to-side-e": "window-management",
    "move-to-side-w": "window-management",
    "begin-move": "window-management",
    "begin-resize": "window-management",
    # Navigation
    "switch-windows": "navigation",
    "switch-windows-backward": "navigation",
    "switch-applications": "navigation",
    "switch-applications-backward": "navigation",
    "switch-group": "navigation",
    "switch-group-backward": "navigation",
    "cycle-windows": "navigation",
    "cycle-windows-backward": "navigation",
    "cycle-group": "navigation",
    "cycle-group-backward": "navigation",
    "switch-to-workspace-1": "navigation",
    "switch-to-workspace-2": "navigation",
    "switch-to-workspace-3": "navigation",
    "switch-to-workspace-4": "navigation",
    "switch-to-workspace-5": "navigation",
    "switch-to-workspace-6": "navigation",
    "switch-to-workspace-7": "navigation",
    "switch-to-workspace-8": "navigation",
    "switch-to-workspace-9": "navigation",
    "switch-to-workspace-10": "navigation",
    "switch-to-workspace-left": "navigation",
    "switch-to-workspace-right": "navigation",
    "switch-to-workspace-up": "navigation",
    "switch-to-workspace-down": "navigation",
    "switch-to-workspace-last": "navigation",
    "move-to-workspace-1": "navigation",
    "move-to-workspace-2": "navigation",
    "move-to-workspace-3": "navigation",
    "move-to-workspace-4": "navigation",
    "move-to-workspace-5": "navigation",
    "move-to-workspace-6": "navigation",
    "move-to-workspace-7": "navigation",
    "move-to-workspace-8": "navigation",
    "move-to-workspace-9": "navigation",
    "move-to-workspace-10": "navigation",
    "move-to-workspace-left": "navigation",
    "move-to-workspace-right": "navigation",
    "move-to-workspace-up": "navigation",
    "move-to-workspace-down": "navigation",
    "move-to-workspace-last": "navigation",
    "move-to-monitor-left": "navigation",
    "move-to-monitor-right": "navigation",
    "move-to-monitor-up": "navigation",
    "move-to-monitor-down": "navigation",
    # Shell
    "toggle-overview": "shell",
    "toggle-application-view": "shell",
    "toggle-message-tray": "shell",
    "focus-active-notification": "shell",
    "show-screenshot-ui": "shell",
    "show-screen-recording-ui": "shell",
    "screenshot": "shell",
    "screenshot-window": "shell",
    "open-application-menu": "shell",
    "switch-input-source": "shell",
    "switch-input-source-backward": "shell",
    # System
    "screensaver": "system",
    "logout": "system",
    "power": "system",
    "suspend": "system",
    "hibernate": "system",
    "lock-screen": "system",
    # Media
    "play": "media",
    "pause": "media",
    "stop": "media",
    "previous": "media",
    "next": "media",
    "volume-up": "media",
    "volume-down": "media",
    "volume-mute": "media",
    "mic-mute": "media",
    "eject": "media",
    "media": "media",
    # Accessibility
    "increase-text-size": "accessibility",
    "decrease-text-size": "accessibility",
    "toggle-contrast": "accessibility",
    "magnifier": "accessibility",
    "magnifier-zoom-in": "accessibility",
    "magnifier-zoom-out": "accessibility",
    "screenreader": "accessibility",
    "on-screen-keyboard": "accessibility",
}


def _parses_as_key_binding(value: str) -> bool:
    """Return True if value is a valid GTK accelerator (with or without modifiers)."""
    from gi.repository import Gtk

    try:
        result = Gtk.accelerator_parse(value)
        if isinstance(result, tuple) and len(result) >= 3 and result[0]:
            return result[1] != 0  # keyval must be non-zero
    except Exception:
        pass
    return False


def _humanize_key_name(key: str) -> str:
    """Convert a GSettings key name to a human-readable label."""
    name = key

    # Strip "-static" suffix (GNOME media key variants)
    if name.endswith("-static"):
        name = name[:-7]

    # Media key friendly names
    media_names = {
        "next": "Next Track",
        "previous": "Previous Track",
        "play": "Play/Pause",
        "pause": "Pause",
        "stop": "Stop",
        "eject": "Eject",
        "playback-forward": "Fast Forward",
        "playback-rewind": "Rewind",
        "playback-random": "Shuffle",
        "playback-repeat": "Repeat",
    }

    if name in media_names:
        return media_names[name]

    # Strip prefixes that become redundant with group headers
    prefixes_to_strip = [
        "switch-to-workspace-",
        "move-to-workspace-",
        "switch-to-",
        "move-to-",
        "switch-",
        "toggle-tiled-",
        "toggle-",
        "begin-",
        "cycle-",
        "volume-",
        "show-",
        "tile-",
        "activate-",
    ]

    for prefix in prefixes_to_strip:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break

    # Skip "-ignore-ta" suffix (Tiling Assistant internal)
    if name.endswith("-ignore-ta"):
        return ""

    # Friendly name mappings for tiling
    tiling_names = {
        "left-half": "Left Half",
        "right-half": "Right Half",
        "top-half": "Top Half",
        "bottom-half": "Bottom Half",
        "topleft-quarter": "Top Left",
        "topright-quarter": "Top Right",
        "bottomleft-quarter": "Bottom Left",
        "bottomright-quarter": "Bottom Right",
        "maximize": "Maximize",
        "maximize-horizontally": "Maximize Horizontal",
        "maximize-vertically": "Maximize Vertical",
        "center-window": "Center Window",
        "restore-window": "Restore Window",
        "edit-mode": "Edit Mode",
    }

    if name in tiling_names:
        return tiling_names[name]

    # Handle layout0-19 -> Layout 1-20
    if name.startswith("layout"):
        try:
            num = int(name[6:]) + 1
            return f"Layout {num}"
        except ValueError:
            pass

    # Convert to readable format
    name = name.replace("-", " ").replace("_", " ")

    # Capitalize words (short connectors keep their case once a word precedes them)
    words = name.split()
    humanized = []
    for word in words:
        if len(word) <= 2 and humanized:
            humanized.append(word)
        else:
            humanized.append(word.capitalize())

    return " ".join(humanized)


def _get_shortcut_group(key: str) -> str:
    """Determine the display group for a shortcut within its category."""
    # Skip internal tiling assistant keys
    if key.endswith("-ignore-ta"):
        return "Internal"

    # Tiling - halves
    if any(
        x in key
        for x in (
            "left-half",
            "right-half",
            "top-half",
            "bottom-half",
            "toggle-tiled-left",
            "toggle-tiled-right",
        )
    ):
        return "Tile Halves"

    # Tiling - quarters
    if any(x in key for x in ("quarter", "corner")):
        return "Tile Quarters"

    # Tiling - layouts
    if key.startswith("activate-layout"):
        return "Layouts"

    # Tiling - other
    if key in (
        "tile-maximize",
        "tile-maximize-horizontally",
        "tile-maximize-vertically",
        "center-window",
        "restore-window",
        "tile-edit-mode",
        "auto-tile",
    ):
        return "Tile Actions"

    # Workspace switching
    if key.startswith("switch-to-workspace"):
        return "Switch Workspace"
    if key.startswith("move-to-workspace"):
        return "Move to Workspace"

    # Monitor management
    if key.startswith("move-to-monitor"):
        return "Move to Monitor"

    # Window switching
    if key.startswith("switch-") or key.startswith("cycle-"):
        return "Switch Windows"

    # Window positioning (native GNOME)
    if key.startswith("move-to-side"):
        return "Tile Halves"
    if key.startswith("move-to-corner") or key == "move-to-center":
        return "Tile Quarters"

    # Window state toggles
    if key in (
        "maximize",
        "minimize",
        "unmaximize",
        "toggle-maximized",
        "maximize-horizontally",
        "maximize-vertically",
        "toggle-fullscreen",
    ):
        return "Window State"

    # Window operations
    if key in (
        "close",
        "always-on-top",
        "toggle-above",
        "raise",
        "lower",
        "begin-move",
        "begin-resize",
    ):
        return "Window Actions"

    # Volume controls
    if key.startswith("volume-") or key in ("mic-mute",):
        return "Volume"

    # Media playback
    base_key = key[:-7] if key.endswith("-static") else key
    if base_key in (
        "play",
        "pause",
        "stop",
        "previous",
        "next",
        "media",
        "eject",
    ) or base_key.startswith("playback-"):
        return "Playback"

    # Screenshots
    if "screenshot" in key or "screen-recording" in key:
        return "Screenshots"

    # Shell toggles
    if key.startswith("toggle-") or key.startswith("show-"):
        return "Shell Actions"

    # System actions
    if key in ("screensaver", "logout", "power", "suspend", "hibernate", "lock-screen"):
        return "System"

    # Accessibility
    if any(x in key for x in ("magnifier", "screenreader", "text-size", "contrast", "keyboard")):
        return "Accessibility"

    # Input source
    if "input-source" in key:
        return "Input"

    return "Other"


def _get_key_category(key: str) -> str:
    """Determine category for a key based on its name."""
    if key in KEY_CATEGORIES:
        return KEY_CATEGORIES[key]

    if any(key.startswith(p) for p in ["switch-to-", "move-to-", "switch-"]):
        return "navigation"
    if any(key.startswith(p) for p in ["volume-", "mic-", "media-"]):
        return "media"
    if any(key.startswith(p) for p in ["toggle-", "show-"]):
        return "shell"
    if any(
        key.startswith(p) for p in ["begin-", "maximize", "minimize", "close", "raise", "lower"]
    ):
        return "window-management"

    return "custom"


class GnomeShortcutsBackend(ShortcutsBackend):
    """GNOME shortcuts backend using GSettings/dconf."""

    CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
    CUSTOM_BINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
    CUSTOM_PATH_PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"

    def __init__(self) -> None:
        self._settings_cache: dict[str, Gio.Settings] = {}
        self._schema_source = self._build_schema_source()

    @staticmethod
    def _build_schema_source() -> Gio.SettingsSchemaSource:
        """Build a schema source that includes GNOME extension schemas."""
        source = Gio.SettingsSchemaSource.get_default()
        ext_base = os.path.expanduser("~/.local/share/gnome-shell/extensions")
        for schema_dir in sorted(glob.glob(f"{ext_base}/*/schemas")):
            if os.path.isdir(schema_dir):
                try:
                    source = Gio.SettingsSchemaSource.new_from_directory(schema_dir, source, False)
                except Exception:
                    pass
        return source

    def _get_settings(self, schema_id: str, path: str | None = None) -> Gio.Settings | None:
        """Get or create GSettings for a schema, optionally with a path for relocatable schemas."""
        cache_key = f"{schema_id}:{path}" if path else schema_id
        if cache_key in self._settings_cache:
            return self._settings_cache[cache_key]

        # Check if schema exists in our composite source (includes extension schemas)
        if not self._schema_source:
            return None
        schema = self._schema_source.lookup(schema_id, True)
        if not schema:
            return None

        try:
            # Use new_full so we honour our composite source (covers extension schemas
            # that Gio.Settings.new() can't find via the default source alone)
            settings = Gio.Settings.new_full(schema, None, path)
            if settings:
                self._settings_cache[cache_key] = settings
            return settings
        except Exception:
            return None

    def _is_shortcut_key(self, schema: Gio.SettingsSchema, key: str) -> bool:
        """Check if a key is a shortcut binding."""
        key_obj = schema.get_key(key)
        if not key_obj:
            return False

        non_shortcut_patterns = [
            "-ignore-ta",
            "-color",
            "-size",
            "-low-performance-mode",
            "-auto-tile-mode",
            "-behavior",
            "-rects",
            "enable-",
            "disable-",
            "default-",
            "debugging-",
            "dynamic-",
            "favorite-",
            "active-window-hint",
            "adapt-",
            "low-performance",
            "restore-window-size",
        ]
        if any(p in key for p in non_shortcut_patterns):
            return False

        variant_type = key_obj.get_value_type()
        type_string = variant_type.dup_string()

        if type_string not in ("as", "s"):
            return False

        if type_string == "as":
            default = key_obj.get_default_value()
            if default:
                values = default.unpack()
                if values and not any(
                    _parses_as_key_binding(v)  # Valid accelerator (with or without modifiers)
                    for v in values
                ):
                    return False

        return True

    def _get_default_bindings(self, schema: Gio.SettingsSchema, key: str) -> list[KeyBinding]:
        """Get default bindings for a key."""
        key_obj = schema.get_key(key)
        if not key_obj:
            return []

        default = key_obj.get_default_value()
        return self._parse_binding_value(default)

    def _parse_binding_value(self, value: GLib.Variant | None) -> list[KeyBinding]:
        """Parse a GSettings value to key bindings."""
        if not value:
            return []

        bindings = []
        type_string = value.get_type_string()

        if type_string == "as":
            for accel in value.unpack():
                if accel and accel != "disabled":
                    binding = KeyBinding.from_accelerator(accel)
                    if binding:
                        bindings.append(binding)
        elif type_string == "s":
            accel = value.unpack()
            if accel and accel != "disabled":
                binding = KeyBinding.from_accelerator(accel)
                if binding:
                    bindings.append(binding)

        return bindings

    # --- ShortcutsBackend Implementation ---

    def get_categories(self) -> list[ShortcutCategory]:
        """Get all shortcut categories."""
        return CATEGORIES

    def load_all_shortcuts(self) -> dict[str, Shortcut]:
        """Load all shortcuts from known schemas."""
        shortcuts: dict[str, Shortcut] = {}

        for schema_info in SHORTCUT_SCHEMAS:
            schema_id = schema_info["schema"]
            default_category = schema_info["category"]

            settings = self._get_settings(schema_id)
            if not settings:
                continue

            schema = self._schema_source.lookup(schema_id, True)
            if not schema:
                continue

            for key in schema.list_keys():
                if not self._is_shortcut_key(schema, key):
                    continue

                shortcut_id = f"{schema_id}.{key}"

                current_value = settings.get_value(key)
                current_bindings = self._parse_binding_value(current_value)
                default_bindings = self._get_default_bindings(schema, key)

                category = _get_key_category(key)
                if category == "custom":
                    category = default_category

                key_obj = schema.get_key(key)
                description = key_obj.get_description() if key_obj else ""

                allow_multiple = current_value and current_value.get_type_string() == "as"

                name = _humanize_key_name(key)
                if not name:
                    continue

                group = _get_shortcut_group(key)
                if group == "Internal":
                    continue

                shortcut = Shortcut(
                    id=shortcut_id,
                    name=name,
                    description=description or "",
                    category=category,
                    group=group,
                    schema=schema_id,
                    key=key,
                    bindings=current_bindings,
                    default_bindings=default_bindings,
                    allow_multiple=allow_multiple,
                )

                shortcuts[shortcut_id] = shortcut

        # Load custom keybindings
        custom_shortcuts = self._load_custom_shortcuts()
        shortcuts.update(custom_shortcuts)

        return shortcuts

    def _load_custom_shortcuts(self) -> dict[str, Shortcut]:
        """Load custom keybindings as Shortcut objects."""
        shortcuts = {}

        for binding in self.get_custom_keybindings():
            path = binding["path"]
            name = binding["name"]
            command = binding["command"]
            accel = binding["binding"]

            shortcut_id = f"custom:{path}"

            bindings = []
            if accel:
                kb = KeyBinding.from_accelerator(accel)
                if kb:
                    bindings.append(kb)

            name_lower = (name or "").lower()
            group = "Screenshots" if "screenshot" in name_lower else "Launchers"

            shortcut = Shortcut(
                id=shortcut_id,
                name=name or "Custom Shortcut",
                description=command or "",
                category="custom",
                group=group,
                schema="custom",
                key=path,
                bindings=bindings,
                default_bindings=[],
                allow_multiple=False,
            )

            shortcuts[shortcut_id] = shortcut

        return shortcuts

    def save_shortcut(self, shortcut: Shortcut) -> bool:
        """Save a shortcut binding to GSettings."""
        if shortcut.schema == "custom":
            path = shortcut.key
            accel = shortcut.accelerators[0] if shortcut.accelerators else ""
            return self.update_custom_keybinding(path, binding=accel)

        settings = self._get_settings(shortcut.schema)
        if not settings:
            return False

        schema = self._schema_source.lookup(shortcut.schema, True)
        if not schema:
            return False

        key_obj = schema.get_key(shortcut.key)
        if not key_obj:
            return False

        variant_type = key_obj.get_value_type()
        type_string = variant_type.dup_string()

        accelerators = shortcut.accelerators

        if type_string == "as":
            if not accelerators:
                value = GLib.Variant("as", ["disabled"])
            else:
                value = GLib.Variant("as", accelerators)
        elif type_string == "s":
            if not accelerators:
                value = GLib.Variant("s", "disabled")
            else:
                value = GLib.Variant("s", accelerators[0])
        else:
            return False

        settings.set_value(shortcut.key, value)
        return True

    def find_conflicts(self, binding: KeyBinding, exclude_id: str | None = None) -> list[Shortcut]:
        """Find shortcuts that conflict with a binding."""
        conflicts = []
        all_shortcuts = self.load_all_shortcuts()

        for shortcut in all_shortcuts.values():
            if exclude_id and shortcut.id == exclude_id:
                continue

            if binding in shortcut.bindings:
                conflicts.append(shortcut)

        return conflicts

    def reset_shortcut(self, shortcut: Shortcut) -> bool:
        """Reset a shortcut to its default value."""
        settings = self._get_settings(shortcut.schema)
        if not settings:
            return False

        settings.reset(shortcut.key)
        shortcut.reset()
        return True

    # --- Custom Keybindings ---

    def get_custom_keybindings(self) -> list[dict]:
        """Get all custom keybindings."""
        settings = self._get_settings(self.CUSTOM_SCHEMA)
        if not settings:
            return []

        paths = settings.get_strv("custom-keybindings")
        result = []

        for path in paths:
            try:
                # Use _get_settings with path to properly use host schema source
                binding_settings = self._get_settings(self.CUSTOM_BINDING_SCHEMA, path)
                if not binding_settings:
                    continue

                name = binding_settings.get_string("name")
                command = binding_settings.get_string("command")
                binding = binding_settings.get_string("binding")

                # Include all custom shortcuts, even without bindings
                # so users can configure them
                result.append(
                    {
                        "path": path,
                        "name": name,
                        "command": command,
                        "binding": binding,
                    }
                )
            except Exception:
                # Schema not accessible (likely Flatpak sandbox issue)
                continue

        return result

    def add_custom_keybinding(self, name: str, command: str, binding: str) -> str | None:
        """Add a custom keybinding."""
        settings = self._get_settings(self.CUSTOM_SCHEMA)
        if not settings:
            return None

        paths = settings.get_strv("custom-keybindings")
        existing_nums = set()
        for path in paths:
            try:
                num = int(path.rstrip("/").split("custom")[-1])
                existing_nums.add(num)
            except ValueError:
                pass

        num = 0
        while num in existing_nums:
            num += 1

        new_path = f"{self.CUSTOM_PATH_PREFIX}/custom{num}/"

        binding_settings = self._get_settings(self.CUSTOM_BINDING_SCHEMA, new_path)
        if not binding_settings:
            return None
        binding_settings.set_string("name", name)
        binding_settings.set_string("command", command)
        binding_settings.set_string("binding", binding)

        paths.append(new_path)
        settings.set_strv("custom-keybindings", paths)

        return new_path

    def update_custom_keybinding(
        self,
        path: str,
        name: str | None = None,
        command: str | None = None,
        binding: str | None = None,
    ) -> bool:
        """Update an existing custom keybinding."""
        binding_settings = self._get_settings(self.CUSTOM_BINDING_SCHEMA, path)
        if not binding_settings:
            return False

        if name is not None:
            binding_settings.set_string("name", name)
        if command is not None:
            binding_settings.set_string("command", command)
        if binding is not None:
            binding_settings.set_string("binding", binding)

        return True

    def delete_custom_keybinding(self, path: str) -> bool:
        """Delete a custom keybinding."""
        settings = self._get_settings(self.CUSTOM_SCHEMA)
        if not settings:
            return False

        paths = settings.get_strv("custom-keybindings")
        if path not in paths:
            return False

        binding_settings = self._get_settings(self.CUSTOM_BINDING_SCHEMA, path)
        if binding_settings:
            binding_settings.reset("name")
            binding_settings.reset("command")
            binding_settings.reset("binding")

        paths.remove(path)
        settings.set_strv("custom-keybindings", paths)

        return True

    # --- App Detection (for default shortcuts setup) ---

    def detect_terminal(self) -> str | None:
        """Detect the best terminal emulator."""
        terminals = [
            ("ghostty", "ghostty"),
            ("kitty", "kitty"),
            ("alacritty", "alacritty"),
            ("wezterm", "wezterm start"),
            ("foot", "foot"),
            ("gnome-terminal", "gnome-terminal"),
            ("kgx", "kgx"),
            ("tilix", "tilix"),
            ("terminator", "terminator"),
            ("xfce4-terminal", "xfce4-terminal"),
            ("konsole", "konsole --new-tab"),
            ("xterm", "xterm"),
        ]

        for binary, command in terminals:
            if shutil.which(binary):
                return command

        if shutil.which("x-terminal-emulator"):
            return "x-terminal-emulator"

        return None

    def detect_file_manager(self) -> str | None:
        """Detect the default file manager."""
        # Check standard ones first via shutil.which for test compatibility
        file_managers = [
            ("nautilus", "nautilus --new-window"),
            ("nemo", "nemo --new-window"),
            ("thunar", "thunar"),
            ("dolphin", "dolphin --new-window"),
            ("pcmanfm", "pcmanfm --new-win"),
            ("caja", "caja --new-window"),
        ]

        for binary, command in file_managers:
            if shutil.which(binary):
                return command

        try:
            result = subprocess.run(
                ["xdg-mime", "query", "default", "inode/directory"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                desktop_file = result.stdout.strip()
                if "nautilus" in desktop_file:
                    return "nautilus --new-window"
                elif "thunar" in desktop_file:
                    return "thunar"
                elif "dolphin" in desktop_file:
                    return "dolphin --new-window"
                elif "nemo" in desktop_file:
                    return "nemo --new-window"
                elif "pcmanfm" in desktop_file:
                    return "pcmanfm --new-win"
                elif "caja" in desktop_file:
                    return "caja --new-window"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None

    def find_custom_keybinding(self, name: str) -> dict | None:
        """Find a custom keybinding by name."""
        for binding in self.get_custom_keybindings():
            if binding["name"] == name:
                return binding
        return None

    def find_custom_keybinding_by_type(self, app_type: str) -> dict | None:
        """Find a custom keybinding by application type (terminal, browser, etc)."""
        type_keywords = {
            "terminal": ["terminal", "kitty", "alacritty", "foot", "kgx", "term"],
            "browser": ["browser", "firefox", "chrome", "chromium", "brave", "vivaldi", "web"],
            "file_manager": ["files", "nautilus", "nemo", "thunar", "dolphin", "pcmanfm"],
            # Keywords must not overlap between nightpanel's own entries. "nightpanel" and
            # "player" used to match each other's commands, so the cheat-sheet step rewrote
            # the mini-player's entry and Super+P was lost.
            "music": ["music", "spotify", "tidal", "rhythmbox"],
            "player": ["--player", "nightpanel-player", "nightpanel player", "launch player"],
            "cheat_sheet": ["cheat"],
            "screenshot_full_file": ["screenshot to file"],
            "screenshot_full_clipboard": ["screenshot to clipboard"],
            "screenshot_area_file": ["screenshot area to file"],
            "screenshot_area_clipboard": ["screenshot area to clipboard"],
        }

        keywords = type_keywords.get(app_type, [])
        for binding in self.get_custom_keybindings():
            name = binding["name"].lower()
            command = binding["command"].lower()
            if any(k in name or k in command for k in keywords):
                return binding
        return None

    def detect_browser(self) -> str | None:
        """Detect the default browser."""
        try:
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                desktop_file = result.stdout.strip()
                if "firefox" in desktop_file:
                    return "firefox --new-window"
                elif "chrome" in desktop_file or "chromium" in desktop_file:
                    return (
                        "google-chrome --new-window"
                        if shutil.which("google-chrome")
                        else "chromium --new-window"
                    )
                elif "brave" in desktop_file:
                    return "brave --new-window"
                elif "vivaldi" in desktop_file:
                    return "vivaldi --new-window"
                elif "epiphany" in desktop_file or "gnome-web" in desktop_file:
                    return "epiphany --new-window"
                elif "zen" in desktop_file:
                    return "zen-browser --new-window"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        browsers = [
            ("firefox", "firefox --new-window"),
            ("google-chrome", "google-chrome --new-window"),
            ("google-chrome-stable", "google-chrome-stable --new-window"),
            ("chromium", "chromium --new-window"),
            ("chromium-browser", "chromium-browser --new-window"),
            ("brave-browser", "brave-browser --new-window"),
            ("vivaldi", "vivaldi --new-window"),
            ("epiphany", "epiphany --new-window"),
            ("zen-browser", "zen-browser --new-window"),
        ]

        for binary, command in browsers:
            if shutil.which(binary):
                return command

        return None

    def detect_music_player(self) -> str | None:
        """Detect installed music player."""
        try:
            result = subprocess.run(
                ["flatpak", "info", "com.spotify.Client"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "flatpak run com.spotify.Client"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if shutil.which("spotify"):
            return "spotify"

        try:
            result = subprocess.run(
                ["flatpak", "info", "com.mastermindzh.tidal-hifi"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "flatpak run com.mastermindzh.tidal-hifi"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        players = [
            ("rhythmbox", "rhythmbox"),
            ("gnome-music", "gnome-music"),
            ("lollypop", "lollypop"),
            ("elisa", "elisa"),
            ("audacious", "audacious"),
            ("clementine", "clementine"),
            ("strawberry", "strawberry"),
            ("amberol", "amberol"),
        ]

        for binary, command in players:
            if shutil.which(binary):
                return command

        return None

    def detect_np_player(self) -> str | None:
        """Detect how to launch nightpanel's own theme-synced mini-player.

        Mirrors detect_nightpanel(): Flatpak first (app ID intentionally kept as
        DailyDriver — see CLAUDE.md), then the dedicated ``nightpanel-player``
        binary, then ``nightpanel --player``, then a dev checkout's run-dev.sh.
        All route to the standalone PlayerApp (the flag forms via
        ``application.main()``'s ``--player`` intercept). Returns the launch
        command, or None if nightpanel isn't found.
        """
        try:
            result = subprocess.run(
                ["flatpak", "info", "io.github.gregfelice.DailyDriver"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "flatpak run io.github.gregfelice.DailyDriver --player"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if shutil.which("nightpanel-player"):
            return "nightpanel-player"

        if shutil.which("nightpanel"):
            return "nightpanel --player"

        curr = Path.cwd()
        for _ in range(3):
            dev_script = curr / "run-dev.sh"
            if dev_script.exists():
                return f"{dev_script} --player"
            curr = curr.parent

        return None

    # --- Workspace Management ---

    def get_workspace_count(self) -> int:
        """Get the current number of workspaces."""
        settings = self._get_settings("org.gnome.desktop.wm.preferences")
        if settings:
            return settings.get_int("num-workspaces")
        return 4  # GNOME default

    def set_workspace_count(self, count: int) -> bool:
        """Set the number of workspaces."""
        settings = self._get_settings("org.gnome.desktop.wm.preferences")
        if settings:
            settings.set_int("num-workspaces", count)
            return True
        return False

    def is_dynamic_workspaces(self) -> bool:
        """Check if dynamic workspaces mode is enabled."""
        settings = self._get_settings("org.gnome.mutter")
        if settings:
            return settings.get_boolean("dynamic-workspaces")
        return True  # GNOME default

    def set_dynamic_workspaces(self, enabled: bool) -> bool:
        """Enable or disable dynamic workspaces."""
        settings = self._get_settings("org.gnome.mutter")
        if settings:
            settings.set_boolean("dynamic-workspaces", enabled)
            return True
        return False

    def setup_workspaces_for_hyprland(self) -> bool:
        """Configure 10 fixed workspaces for Hyprland-style preset."""
        success = self.set_dynamic_workspaces(False)
        success = self.set_workspace_count(10) and success
        return success

    def restore_default_workspaces(self) -> bool:
        """Restore GNOME default workspace settings (4 dynamic)."""
        success = self.set_workspace_count(4)
        success = self.set_dynamic_workspaces(True) and success
        return success

    def has_hyprland_workspace_setup(self) -> bool:
        """Check if workspaces are configured for Hyprland-style (10 fixed)."""
        return self.get_workspace_count() == 10 and not self.is_dynamic_workspaces()

    def detect_nightpanel(self) -> str | None:
        """Detect DailyDriver installation."""
        try:
            # Flatpak app ID is still io.github.gregfelice.DailyDriver (the manifest
            # name was deliberately kept for the in-flight Flathub PR — see CLAUDE.md).
            # The rename swept this to "Nightpanel", which silently broke Flatpak
            # cheat-sheet detection since that app ID is never published.
            result = subprocess.run(
                ["flatpak", "info", "io.github.gregfelice.DailyDriver"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "flatpak run io.github.gregfelice.DailyDriver --cheat-sheet"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if shutil.which("nightpanel"):
            return "nightpanel --cheat-sheet"

        # Check for development environment
        # Search upwards for run-dev.sh
        curr = Path.cwd()
        for _ in range(3):
            dev_script = curr / "run-dev.sh"
            if dev_script.exists():
                return f"{dev_script} --cheat-sheet"
            curr = curr.parent

        return None

    PLAYER_BINDING = "<Super>p"

    @staticmethod
    def _player_seed_marker() -> Path:
        return Paths.detect().nightpanel / "player-shortcut-seeded"

    def ensure_player_shortcut(self) -> str:
        """Bind Super+P to nightpanel's mini-player, once.

        Idempotent and safe to run at every login. It adds the binding only when
        nightpanel has never seeded it, nobody has a player entry, and Super+P is
        free. A user who deletes the entry is not given it back: the marker file
        records that the default was already offered.
        """
        marker = self._player_seed_marker()
        found = self.find_custom_keybinding_by_type("player")
        if found:
            # Matched by name too, so an entry whose command was overwritten (the
            # cheat-sheet step used to do that) still counts as the player and is repaired.
            if any(k in found["command"] for k in ("--player", "nightpanel-player")):
                return "present"
            command = self.detect_np_player()
            if command and self.update_custom_keybinding(found["path"], command=command):
                return f"repaired: {found['command']!r} -> {command!r}"
            return "present"
        if marker.exists():
            return "skipped: seeded before and since removed"
        command = self.detect_np_player()
        if not command:
            return "skipped: nightpanel player not found"
        for existing in self.get_custom_keybindings():
            if existing["binding"] == self.PLAYER_BINDING:
                return f"skipped: Super+P is already {existing['name']!r}"
        if not self.add_custom_keybinding("Launch Player", command, self.PLAYER_BINDING):
            return "failed: could not write the keybinding"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return f"added: {command}"

    def setup_default_custom_shortcuts(self) -> dict[str, str]:
        """Set up default custom shortcuts for common applications.

        Returns:
            Dict mapping app type to result message.
        """
        results: dict[str, str] = {}

        # Terminal
        terminal = self.detect_terminal()
        if terminal:
            existing = self.find_custom_keybinding_by_type("terminal")
            if existing:
                self.update_custom_keybinding(
                    existing["path"], command=terminal, binding="<Super>Return"
                )
                results["terminal"] = f"Updated: {terminal}"
            else:
                path = self.add_custom_keybinding("Launch Terminal", terminal, "<Super>Return")
                results["terminal"] = f"Added: {terminal}" if path else "Failed to add"
        else:
            results["terminal"] = "No terminal found"

        # File manager
        file_manager = self.detect_file_manager()
        if file_manager:
            existing = self.find_custom_keybinding_by_type("file_manager")
            if existing:
                self.update_custom_keybinding(
                    existing["path"], command=file_manager, binding="<Super>e"
                )
                results["file_manager"] = f"Updated: {file_manager}"
            else:
                path = self.add_custom_keybinding("Launch Files", file_manager, "<Super>e")
                results["file_manager"] = f"Added: {file_manager}" if path else "Failed to add"
        else:
            results["file_manager"] = "No file manager found"

        # Browser
        browser = self.detect_browser()
        if browser:
            existing = self.find_custom_keybinding_by_type("browser")
            if existing:
                self.update_custom_keybinding(existing["path"], command=browser, binding="<Super>b")
                results["browser"] = f"Updated: {browser}"
            else:
                path = self.add_custom_keybinding("Launch Browser", browser, "<Super>b")
                results["browser"] = f"Added: {browser}" if path else "Failed to add"
        else:
            results["browser"] = "No browser found"

        # Music player — prefer nightpanel's own theme-synced mini-player so
        # Super+P opens the NP player; fall back to a detected external player
        # (Spotify/rhythmbox/…) where the NP player isn't installed.
        music = self.detect_np_player() or self.detect_music_player()
        if music:
            existing = self.find_custom_keybinding_by_type(
                "player"
            ) or self.find_custom_keybinding_by_type("music")
            if existing:
                self.update_custom_keybinding(existing["path"], command=music, binding="<Super>p")
                results["music"] = f"Updated: {music}"
            else:
                path = self.add_custom_keybinding("Launch Music", music, "<Super>p")
                results["music"] = f"Added: {music}" if path else "Failed to add"
        else:
            results["music"] = "No music player found"

        # Cheat sheet
        nightpanel_cmd = self.detect_nightpanel()
        if nightpanel_cmd:
            existing = self.find_custom_keybinding_by_type("cheat_sheet")
            if existing:
                self.update_custom_keybinding(
                    existing["path"], command=nightpanel_cmd, binding="<Alt><Super>slash"
                )
                results["cheat_sheet"] = f"Updated: {nightpanel_cmd}"
            else:
                path = self.add_custom_keybinding(
                    "Keyboard Cheat Sheet", nightpanel_cmd, "<Alt><Super>slash"
                )
                results["cheat_sheet"] = f"Added: {nightpanel_cmd}" if path else "Failed to add"
        else:
            results["cheat_sheet"] = "Nightpanel not found"

        # Screenshot: full screen → file (Alt+Shift+Super+S)
        existing = self.find_custom_keybinding_by_type("screenshot_full_file")
        if existing:
            self.update_custom_keybinding(
                existing["path"], command="gnome-screenshot", binding="<Alt><Shift><Super>s"
            )
            results["screenshot_full_file"] = "updated"
        else:
            path = self.add_custom_keybinding(
                "screenshot to file", "gnome-screenshot", "<Alt><Shift><Super>s"
            )
            results["screenshot_full_file"] = "added" if path else "Failed to add"

        # Screenshot: full screen → clipboard (Ctrl+Shift+Super+S)
        existing = self.find_custom_keybinding_by_type("screenshot_full_clipboard")
        if existing:
            self.update_custom_keybinding(
                existing["path"], command="gnome-screenshot -c", binding="<Control><Shift><Super>s"
            )
            results["screenshot_full_clipboard"] = "updated"
        else:
            path = self.add_custom_keybinding(
                "screenshot to clipboard", "gnome-screenshot -c", "<Control><Shift><Super>s"
            )
            results["screenshot_full_clipboard"] = "added" if path else "Failed to add"

        # Screenshot: area → file (Alt+Shift+Super+A)
        existing = self.find_custom_keybinding_by_type("screenshot_area_file")
        if existing:
            self.update_custom_keybinding(
                existing["path"], command="gnome-screenshot -a", binding="<Alt><Shift><Super>a"
            )
            results["screenshot_area_file"] = "updated"
        else:
            path = self.add_custom_keybinding(
                "screenshot area to file", "gnome-screenshot -a", "<Alt><Shift><Super>a"
            )
            results["screenshot_area_file"] = "added" if path else "Failed to add"

        # Screenshot: area → clipboard (Ctrl+Shift+Super+A)
        existing = self.find_custom_keybinding_by_type("screenshot_area_clipboard")
        if existing:
            self.update_custom_keybinding(
                existing["path"],
                command="gnome-screenshot -a -c",
                binding="<Control><Shift><Super>a",
            )
            results["screenshot_area_clipboard"] = "updated"
        else:
            path = self.add_custom_keybinding(
                "screenshot area to clipboard", "gnome-screenshot -a -c", "<Control><Shift><Super>a"
            )
            results["screenshot_area_clipboard"] = "added" if path else "Failed to add"

        return results

# SPDX-License-Identifier: GPL-3.0-or-later
"""KDE Plasma shortcuts backend using kglobalshortcutsrc and kglobalacceld."""

from __future__ import annotations

import configparser
import logging
import shutil
import subprocess
from pathlib import Path

from nightpanel.models import KeyBinding, Shortcut, ShortcutCategory
from nightpanel.paths import Paths
from nightpanel.services.backends.base import ShortcutsBackend

logger = logging.getLogger(__name__)


# Category definitions for KDE
CATEGORIES = [
    ShortcutCategory(
        id="kwin",
        name="Window Management",
        icon="preferences-system-windows-symbolic",
        description="KWin window manager shortcuts",
    ),
    ShortcutCategory(
        id="plasma",
        name="Plasma",
        icon="view-app-grid-symbolic",
        description="Plasma desktop shortcuts",
    ),
    ShortcutCategory(
        id="media",
        name="Media",
        icon="multimedia-player-symbolic",
        description="Media playback and volume controls",
    ),
    ShortcutCategory(
        id="apps",
        name="Applications",
        icon="application-x-addon-symbolic",
        description="Application launchers",
    ),
    ShortcutCategory(
        id="custom",
        name="Custom",
        icon="application-x-addon-symbolic",
        description="User-defined shortcuts",
    ),
]

# Component to category mapping
COMPONENT_CATEGORIES = {
    "kwin": "kwin",
    "KWin": "kwin",
    "ksmserver": "plasma",
    "plasmashell": "plasma",
    "org.kde.krunner.desktop": "apps",
    "org.kde.spectacle.desktop": "plasma",
    "org.kde.dolphin.desktop": "apps",
    "org.kde.konsole.desktop": "apps",
    "kmix": "media",
    "org_kde_powerdevil": "plasma",
    "kded5": "plasma",
    "kded6": "plasma",
    "kaccess": "plasma",
}


def _kde_to_gtk_accelerator(kde_shortcut: str) -> str | None:
    """Convert KDE shortcut format to GTK accelerator format.

    KDE format: "Meta+Return", "Ctrl+Shift+A", "none"
    GTK format: "<Super>Return", "<Control><Shift>a"

    Args:
        kde_shortcut: KDE shortcut string.

    Returns:
        GTK accelerator string, or None if invalid/disabled.
    """
    if not kde_shortcut or kde_shortcut.lower() in ("none", ""):
        return None

    # Handle multiple shortcuts (KDE uses tabs or commas)
    # Take the first one
    if "\t" in kde_shortcut:
        kde_shortcut = kde_shortcut.split("\t")[0]
    if "," in kde_shortcut:
        kde_shortcut = kde_shortcut.split(",")[0]

    parts = kde_shortcut.strip().split("+")
    if not parts:
        return None

    modifiers = []
    key = parts[-1]

    for part in parts[:-1]:
        part_lower = part.lower()
        if part_lower in ("meta", "super"):
            modifiers.append("<Super>")
        elif part_lower in ("ctrl", "control"):
            modifiers.append("<Control>")
        elif part_lower == "shift":
            modifiers.append("<Shift>")
        elif part_lower == "alt":
            modifiers.append("<Alt>")

    return "".join(modifiers) + key


def _gtk_to_kde_accelerator(gtk_accel: str) -> str:
    """Convert GTK accelerator format to KDE shortcut format.

    GTK format: "<Super>Return", "<Control><Shift>a"
    KDE format: "Meta+Return", "Ctrl+Shift+A"
    """
    if not gtk_accel:
        return "none"

    result = gtk_accel
    replacements = [
        ("<Super>", "Meta+"),
        ("<Control>", "Ctrl+"),
        ("<Primary>", "Ctrl+"),
        ("<Shift>", "Shift+"),
        ("<Alt>", "Alt+"),
    ]

    for gtk_mod, kde_mod in replacements:
        result = result.replace(gtk_mod, kde_mod)

    # Remove any remaining angle brackets
    result = result.replace("<", "").replace(">", "")

    return result


class KDEShortcutsBackend(ShortcutsBackend):
    """KDE Plasma shortcuts backend.

    Reads and writes shortcuts from ~/.config/kglobalshortcutsrc and
    optionally communicates with kglobalacceld for live updates.
    """

    def __init__(self) -> None:
        self._config = configparser.ConfigParser()
        self._config.optionxform = str  # Preserve case
        self._load_config()

    @staticmethod
    def _shortcuts_rc() -> Path:
        """KDE shortcut config file, resolved per call so XDG_CONFIG_HOME is honoured."""
        return Paths.detect().config / "kglobalshortcutsrc"

    def _load_config(self) -> None:
        """Load kglobalshortcutsrc config file."""
        rc = self._shortcuts_rc()
        if rc.exists():
            try:
                self._config.read(str(rc))
            except configparser.Error as e:
                logger.error(f"Failed to parse kglobalshortcutsrc: {e}")

    def _save_config(self) -> bool:
        """Save kglobalshortcutsrc config file."""
        try:
            with open(self._shortcuts_rc(), "w") as f:
                self._config.write(f)
            return True
        except OSError as e:
            logger.error(f"Failed to write kglobalshortcutsrc: {e}")
            return False

    def _notify_kglobalaccel(self) -> None:
        """Notify kglobalacceld to reload shortcuts.

        Uses qdbus to call org.kde.KWin.reconfigure.
        """
        try:
            subprocess.run(
                ["qdbus", "org.kde.KWin", "/KWin", "reconfigure"],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _parse_shortcut_value(self, value: str) -> tuple[str, str, str]:
        """Parse a KDE shortcut value.

        Format: "shortcut,default_shortcut,description"
        Example: "Meta+Return,none,Launch Terminal"

        Returns:
            Tuple of (current_shortcut, default_shortcut, description)
        """
        parts = value.split(",", 2)
        current = parts[0] if len(parts) > 0 else ""
        default = parts[1] if len(parts) > 1 else ""
        description = parts[2] if len(parts) > 2 else ""
        return current, default, description

    def _get_component_category(self, component: str) -> str:
        """Get category for a KDE component."""
        for pattern, category in COMPONENT_CATEGORIES.items():
            if pattern.lower() in component.lower():
                return category
        return "apps"

    # --- ShortcutsBackend Implementation ---

    def get_categories(self) -> list[ShortcutCategory]:
        """Get all shortcut categories."""
        return CATEGORIES

    def load_all_shortcuts(self) -> dict[str, Shortcut]:
        """Load all shortcuts from kglobalshortcutsrc."""
        self._load_config()  # Refresh
        shortcuts: dict[str, Shortcut] = {}

        for section in self._config.sections():
            category = self._get_component_category(section)

            for key, value in self._config.items(section):
                # Skip metadata keys
                if key.startswith("_"):
                    continue

                current, default, description = self._parse_shortcut_value(value)

                shortcut_id = f"{section}.{key}"

                # Parse current bindings
                bindings = []
                gtk_accel = _kde_to_gtk_accelerator(current)
                if gtk_accel:
                    binding = KeyBinding.from_accelerator(gtk_accel)
                    if binding:
                        bindings.append(binding)

                # Parse default bindings
                default_bindings = []
                default_gtk = _kde_to_gtk_accelerator(default)
                if default_gtk:
                    default_binding = KeyBinding.from_accelerator(default_gtk)
                    if default_binding:
                        default_bindings.append(default_binding)

                # Create human-readable name from key
                name = key.replace("_", " ").replace("-", " ").title()

                shortcut = Shortcut(
                    id=shortcut_id,
                    name=name,
                    description=description,
                    category=category,
                    group=section,
                    schema=section,  # Use component as schema
                    key=key,
                    bindings=bindings,
                    default_bindings=default_bindings,
                    allow_multiple=False,
                )

                shortcuts[shortcut_id] = shortcut

        return shortcuts

    def save_shortcut(self, shortcut: Shortcut) -> bool:
        """Save a shortcut binding."""
        section = shortcut.schema
        key = shortcut.key

        if not self._config.has_section(section):
            self._config.add_section(section)

        # Get existing value to preserve default and description
        existing = self._config.get(section, key, fallback="none,none,")
        _, default, description = self._parse_shortcut_value(existing)

        # Convert GTK accelerator to KDE format
        if shortcut.accelerators:
            new_shortcut = _gtk_to_kde_accelerator(shortcut.accelerators[0])
        else:
            new_shortcut = "none"

        new_value = f"{new_shortcut},{default},{description}"
        self._config.set(section, key, new_value)

        if self._save_config():
            self._notify_kglobalaccel()
            return True
        return False

    def reset_shortcut(self, shortcut: Shortcut) -> bool:
        """Reset a shortcut to its default value."""
        section = shortcut.schema
        key = shortcut.key

        if not self._config.has_option(section, key):
            return False

        existing = self._config.get(section, key)
        current, default, description = self._parse_shortcut_value(existing)

        # Set current to default
        new_value = f"{default},{default},{description}"
        self._config.set(section, key, new_value)

        if self._save_config():
            shortcut.reset()
            self._notify_kglobalaccel()
            return True
        return False

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

    # --- Custom Keybindings ---
    # KDE handles custom shortcuts through the same kglobalshortcutsrc
    # but in special sections for custom commands

    def get_custom_keybindings(self) -> list[dict]:
        """Get all custom keybindings.

        In KDE, custom keybindings are stored in sections like
        "khotkeys" or application-specific sections.
        """
        result = []

        # Check for khotkeys section (custom shortcuts daemon)
        if self._config.has_section("khotkeys"):
            for key, value in self._config.items("khotkeys"):
                if key.startswith("_"):
                    continue

                current, default, description = self._parse_shortcut_value(value)
                gtk_accel = _kde_to_gtk_accelerator(current)

                result.append(
                    {
                        "path": f"khotkeys/{key}",
                        "name": description or key,
                        "command": "",  # Command stored elsewhere in KDE
                        "binding": gtk_accel or "",
                    }
                )

        return result

    def add_custom_keybinding(self, name: str, command: str, binding: str) -> str | None:
        """Add a custom keybinding.

        Note: Full custom keybinding support in KDE requires editing
        khotkeysrc, which is more complex. This provides basic support.
        """
        if not self._config.has_section("khotkeys"):
            self._config.add_section("khotkeys")

        # Generate a unique key
        existing_keys = set(self._config.options("khotkeys"))
        num = 0
        while f"custom{num}" in existing_keys:
            num += 1

        key = f"custom{num}"
        kde_shortcut = _gtk_to_kde_accelerator(binding)
        value = f"{kde_shortcut},none,{name}"

        self._config.set("khotkeys", key, value)

        if self._save_config():
            self._notify_kglobalaccel()
            return f"khotkeys/{key}"
        return None

    def update_custom_keybinding(
        self,
        path: str,
        name: str | None = None,
        command: str | None = None,
        binding: str | None = None,
    ) -> bool:
        """Update an existing custom keybinding."""
        if "/" not in path:
            return False

        section, key = path.split("/", 1)

        if not self._config.has_option(section, key):
            return False

        existing = self._config.get(section, key)
        current, default, description = self._parse_shortcut_value(existing)

        if binding is not None:
            current = _gtk_to_kde_accelerator(binding)
        if name is not None:
            description = name

        new_value = f"{current},{default},{description}"
        self._config.set(section, key, new_value)

        if self._save_config():
            self._notify_kglobalaccel()
            return True
        return False

    def delete_custom_keybinding(self, path: str) -> bool:
        """Delete a custom keybinding."""
        if "/" not in path:
            return False

        section, key = path.split("/", 1)

        if not self._config.has_option(section, key):
            return False

        self._config.remove_option(section, key)

        if self._save_config():
            self._notify_kglobalaccel()
            return True
        return False

    # --- App Detection ---

    def detect_terminal(self) -> str | None:
        """Detect the best terminal emulator."""
        # KDE-specific terminals first
        terminals = [
            ("konsole", "konsole --new-tab"),
            ("yakuake", "yakuake"),
            ("ghostty", "ghostty"),
            ("kitty", "kitty"),
            ("alacritty", "alacritty"),
            ("wezterm", "wezterm start"),
            ("gnome-terminal", "gnome-terminal"),
            ("xterm", "xterm"),
        ]

        for binary, command in terminals:
            if shutil.which(binary):
                return command

        return None

    def detect_file_manager(self) -> str | None:
        """Detect the default file manager."""
        # Prefer Dolphin on KDE
        if shutil.which("dolphin"):
            return "dolphin --new-window"

        file_managers = [
            ("nautilus", "nautilus --new-window"),
            ("thunar", "thunar"),
            ("nemo", "nemo --new-window"),
            ("pcmanfm-qt", "pcmanfm-qt"),
            ("pcmanfm", "pcmanfm --new-win"),
        ]

        for binary, command in file_managers:
            if shutil.which(binary):
                return command

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
                elif "falkon" in desktop_file:
                    return "falkon"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        browsers = [
            ("firefox", "firefox --new-window"),
            ("google-chrome", "google-chrome --new-window"),
            ("chromium", "chromium --new-window"),
            ("falkon", "falkon"),
            ("brave-browser", "brave-browser --new-window"),
        ]

        for binary, command in browsers:
            if shutil.which(binary):
                return command

        return None

    def detect_music_player(self) -> str | None:
        """Detect installed music player."""
        # Check for Elisa (KDE's music player)
        if shutil.which("elisa"):
            return "elisa"

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

        players = [
            ("strawberry", "strawberry"),
            ("clementine", "clementine"),
            ("amarok", "amarok"),
            ("audacious", "audacious"),
        ]

        for binary, command in players:
            if shutil.which(binary):
                return command

        return None

# SPDX-License-Identifier: GPL-3.0-or-later
"""Zero-friction setup view — tiling, keymap, and keyboard in one screen."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

PRESET_META = {
    "vanilla-gnome": {
        "label": "vanilla gnome",
        "subtitle": "default gnome shortcuts, no tiling keys",
        "tiling": False,
        "hyprland_bundle": False,
    },
    "gnome-tiling": {
        "label": "gnome + tiling",
        "subtitle": "adds tiling extension keymaps to gnome defaults",
        "tiling": True,
        "hyprland_bundle": False,
    },
    "hyprland-style": {
        "label": "hyprland style",
        "subtitle": "keyboard-centric, vim-nav, workspace numbers, tiling",
        "tiling": True,
        "hyprland_bundle": True,
    },
}


class SetupView(Adw.PreferencesPage):
    """Single-screen setup: tiling manager, keymap, and keyboard config."""

    __gtype_name__ = "SetupView"

    def __init__(
        self,
        gsettings_service,
        profile_service,
        kbd_config,
        tiling_service,
        app_settings,
        on_toast: Callable[[str, str | None, Callable | None], None],
        on_shortcuts_reload: Callable[[], None],
        theme_service=None,
        on_brightness_update: Callable[[float], None] | None = None,
        on_video_brightness_update: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__()
        self._gs = gsettings_service
        self._profiles = profile_service
        self._kbd = kbd_config
        self._tiling = tiling_service
        self._settings = app_settings
        self._on_toast = on_toast
        self._on_reload = on_shortcuts_reload
        self._theme = theme_service
        self._on_brightness_update = on_brightness_update
        self._on_video_brightness_update = on_video_brightness_update
        self._loading = True
        self._preset_radios: dict[str, Gtk.CheckButton] = {}

        self._build_ui()
        GLib.idle_add(self._load_state)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        self.set_title("setup")

        # ── Nightpanel ────────────────────────────────────────────────────
        nightpanel_group = Adw.PreferencesGroup()
        nightpanel_group.set_title("nightpanel")
        nightpanel_group.set_description("panel glow intensity")
        self.add(nightpanel_group)

        brightness_row = Adw.ActionRow()
        brightness_row.set_title("brightness")

        self._brightness_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.3, 1.5, 0.05
        )
        self._brightness_scale.set_hexpand(True)
        self._brightness_scale.set_draw_value(False)
        self._brightness_scale.set_valign(Gtk.Align.CENTER)
        self._brightness_scale.set_size_request(200, -1)
        self._brightness_scale.add_mark(0.3, Gtk.PositionType.BOTTOM, None)
        self._brightness_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self._brightness_scale.add_mark(1.5, Gtk.PositionType.BOTTOM, None)
        self._brightness_scale.connect("value-changed", self._on_brightness_changed)
        # Consume scroll events at capture phase before Gtk.Range's built-in
        # scroll-to-adjust handler sees them. Without this, any wheel/touchpad
        # scroll passing through the parent ActionRow drifts the slider
        # without user intent — observed as continuous brightness updates
        # being written even when the user isn't touching the slider.
        _no_scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        _no_scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _no_scroll.connect("scroll", lambda *_: True)
        self._brightness_scale.add_controller(_no_scroll)
        brightness_row.add_suffix(self._brightness_scale)
        nightpanel_group.add(brightness_row)

        # Video brightness — a SEPARATE control for HTML5 <video> (YouTube etc.)
        # dimming via the Firefox bridge. 1.0 = untouched (video stays crisp,
        # the long-standing default); drag down to dim a bright player at night.
        video_brightness_row = Adw.ActionRow()
        video_brightness_row.set_title("video brightness")
        video_brightness_row.set_subtitle("dims YouTube / HTML5 video in Firefox")

        self._video_brightness_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.05
        )
        self._video_brightness_scale.set_hexpand(True)
        self._video_brightness_scale.set_draw_value(False)
        self._video_brightness_scale.set_valign(Gtk.Align.CENTER)
        self._video_brightness_scale.set_size_request(200, -1)
        self._video_brightness_scale.add_mark(0.1, Gtk.PositionType.BOTTOM, None)
        self._video_brightness_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self._video_brightness_scale.connect("value-changed", self._on_video_brightness_changed)
        # Same scroll-capture guard as the brightness slider above — stops
        # wheel/touchpad scroll passing through the row from drifting the value.
        _no_scroll_video = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        _no_scroll_video.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _no_scroll_video.connect("scroll", lambda *_: True)
        self._video_brightness_scale.add_controller(_no_scroll_video)
        video_brightness_row.add_suffix(self._video_brightness_scale)
        nightpanel_group.add(video_brightness_row)

        # ── Tiling Manager ────────────────────────────────────────────────
        tiling_group = Adw.PreferencesGroup()
        tiling_group.set_title("tiling manager")
        tiling_group.set_description("snap and auto-resize windows side-by-side")
        self.add(tiling_group)

        # Status row (read-only)
        self._tiling_status_row = Adw.ActionRow()
        self._tiling_status_row.set_title("tiling assistant")
        self._tiling_status_row.set_subtitle("checking…")
        self._tiling_status_icon = Gtk.Image()
        self._tiling_status_row.add_suffix(self._tiling_status_icon)
        tiling_group.add(self._tiling_status_row)

        # Tile groups (auto-resize adjacent windows)
        self._tile_groups_row = Adw.SwitchRow()
        self._tile_groups_row.set_title("tile groups")
        self._tile_groups_row.set_subtitle("resize adjacent tiled windows together")
        self._tile_groups_row.connect("notify::active", self._on_tile_groups_toggled)
        tiling_group.add(self._tile_groups_row)

        # Raise group together
        self._raise_group_row = Adw.SwitchRow()
        self._raise_group_row.set_title("raise group together")
        self._raise_group_row.set_subtitle("focusing one tiled window raises its group")
        self._raise_group_row.connect("notify::active", self._on_raise_group_toggled)
        tiling_group.add(self._raise_group_row)

        # ── Keymap Style ──────────────────────────────────────────────────
        keymap_group = Adw.PreferencesGroup()
        keymap_group.set_title("keymap style")
        keymap_group.set_description("choose once — applied immediately")
        self.add(keymap_group)

        first_radio: Gtk.CheckButton | None = None
        for key, meta in PRESET_META.items():
            row = Adw.ActionRow()
            if meta["hyprland_bundle"]:
                row.set_title(
                    f'{meta["label"]}  <span size="small" foreground="#26DE81">recommended</span>'
                )
            else:
                row.set_title(meta["label"])
            row.set_subtitle(meta["subtitle"])
            row.set_activatable(True)

            radio = Gtk.CheckButton()
            radio.set_valign(Gtk.Align.CENTER)
            if first_radio:
                radio.set_group(first_radio)
            else:
                first_radio = radio
            row.add_suffix(radio)
            row.set_activatable_widget(radio)
            radio.connect("notify::active", self._on_preset_radio_toggled, key)
            self._preset_radios[key] = radio

            keymap_group.add(row)

        # ── Keyboard Hardware ─────────────────────────────────────────────
        kbd_group = Adw.PreferencesGroup()
        kbd_group.set_title("keyboard")
        self.add(kbd_group)

        self._caps_row = Adw.ComboRow()
        self._caps_row.set_title("caps lock key")
        caps_model = Gtk.StringList()
        for label in ("caps lock", "escape", "control"):
            caps_model.append(label)
        self._caps_row.set_model(caps_model)
        self._caps_row.connect("notify::selected", self._on_caps_selected)
        kbd_group.add(self._caps_row)

    # ------------------------------------------------------------------ state

    def _load_state(self) -> bool:
        self._loading = True

        # Tiling status
        try:
            tiling_info = self._tiling.detect_status()
            from nightpanel.services.tiling_service import TilingStatus

            if tiling_info.status == TilingStatus.TILING_ASSISTANT:
                self._tiling_status_row.set_subtitle("active")
                self._tiling_status_icon.set_from_icon_name("object-select-symbolic")
                self._tiling_status_icon.add_css_class("success")
            else:
                self._tiling_status_row.set_subtitle(
                    "not installed — install from gnome extensions"
                )
                self._tiling_status_icon.set_from_icon_name("dialog-warning-symbolic")
                self._tiling_status_icon.add_css_class("warning")
                self._tile_groups_row.set_sensitive(False)
                self._raise_group_row.set_sensitive(False)
        except Exception:
            pass

        # TA settings
        try:
            ta = self._tiling.get_ta_settings()
            if ta:
                self._tile_groups_row.set_active(ta.get("tile_groups_enabled", True))
                self._raise_group_row.set_active(ta.get("raise_group", True))
        except Exception:
            pass

        # Current preset
        if self._settings:
            current = self._settings.get_string("current-preset")
            if current in self._preset_radios:
                self._preset_radios[current].set_active(True)

        # Caps Lock
        try:
            from nightpanel.services.keyboard_config_service import CapsLockBehavior

            caps = self._kbd.get_caps_lock_behavior()
            idx = {
                CapsLockBehavior.CAPS_LOCK: 0,
                CapsLockBehavior.ESCAPE: 1,
                CapsLockBehavior.CTRL: 2,
            }.get(caps, 0)
            self._caps_row.set_selected(idx)
        except Exception:
            pass

        # Brightness
        if self._settings:
            try:
                brightness = self._settings.get_double("theme-brightness")
                brightness = max(0.3, min(1.5, brightness))
            except Exception:
                brightness = 1.0
        else:
            brightness = self._theme.brightness if self._theme else 1.0
        self._brightness_scale.set_value(brightness)

        # Video brightness (independent of theme-brightness; bridge-only)
        if self._settings:
            try:
                video_brightness = self._settings.get_double("video-brightness")
                video_brightness = max(0.1, min(1.0, video_brightness))
            except Exception:
                video_brightness = 1.0
        else:
            video_brightness = 1.0
        self._video_brightness_scale.set_value(video_brightness)

        self._loading = False
        return False

    # ---------------------------------------------------------------- handlers

    def _on_tile_groups_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._loading:
            return
        enabled = row.get_active()
        try:
            self._tiling.set_tile_groups(enabled)
        except Exception:
            pass

    def _on_raise_group_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._loading:
            return
        enabled = row.get_active()
        try:
            self._tiling.set_raise_group(enabled)
        except Exception:
            pass

    def _on_preset_radio_toggled(self, radio: Gtk.CheckButton, _param, key: str) -> None:
        if self._loading or not radio.get_active():
            return
        self._apply_preset(key)

    def _apply_preset(self, key: str) -> None:
        meta = PRESET_META.get(key, {})
        label = meta.get("label", key)
        is_hyprland = meta.get("hyprland_bundle", False)

        # Remember previous state for undo
        old_key = self._settings.get_string("current-preset") if self._settings else ""
        old_tiling = self._settings.get_boolean("tiling-enabled") if self._settings else True

        def do_apply():
            profile = self._profiles.get_profile(key)
            if not profile:
                return

            # Reset orphaned shortcuts from previous preset
            if old_key and old_key != key:
                old_profile = self._profiles.get_profile(old_key)
                if old_profile:
                    self._profiles.reset_orphaned_shortcuts(old_profile, profile)

            # Apply keymap preset
            self._profiles.apply_profile(profile)

            if is_hyprland:
                # Full Hyprland bundle: 10 workspaces + TA defaults + custom shortcuts
                self._gs.setup_workspaces_for_hyprland()
                self._tiling.apply_hyprland_tiling_settings()
                self._gs.setup_default_custom_shortcuts()

            # Persist app setting
            if self._settings:
                self._settings.set_string("current-preset", key)
                self._settings.set_boolean("tiling-enabled", meta.get("tiling", False))

            GLib.idle_add(self._after_apply, label, old_key, old_tiling, is_hyprland)

        GLib.Thread.new("setup-apply-preset", do_apply)

    def _after_apply(self, label: str, old_key: str, old_tiling: bool, was_hyprland: bool) -> bool:
        self._on_reload()

        def undo():
            if old_key:
                self._loading = True
                if old_key in self._preset_radios:
                    self._preset_radios[old_key].set_active(True)
                self._loading = False
                self._apply_preset(old_key)
                if was_hyprland and old_key != "hyprland-style":
                    self._gs.restore_default_workspaces()

        self._on_toast(f"applied: {label}", "undo", undo)
        return False

    def _on_caps_selected(self, row: Adw.ComboRow, _param) -> None:
        if self._loading:
            return
        from nightpanel.services.keyboard_config_service import CapsLockBehavior

        behaviors = [CapsLockBehavior.CAPS_LOCK, CapsLockBehavior.ESCAPE, CapsLockBehavior.CTRL]
        idx = row.get_selected()
        if 0 <= idx < len(behaviors):
            try:
                self._kbd.set_caps_lock_behavior(behaviors[idx])
            except Exception:
                pass

    def _on_brightness_changed(self, scale: Gtk.Scale) -> None:
        if self._loading:
            return
        value = scale.get_value()
        if self._theme:
            self._theme.set_brightness(value, self._settings)
        if self._on_brightness_update:
            self._on_brightness_update(value)

    def _on_video_brightness_changed(self, scale: Gtk.Scale) -> None:
        # Video brightness is bridge-only — it doesn't touch the GTK theme, so
        # persist straight to settings (no theme_service round-trip) and notify.
        if self._loading:
            return
        value = scale.get_value()
        if self._settings:
            try:
                self._settings.set_double("video-brightness", value)
            except Exception:
                pass
        if self._on_video_brightness_update:
            self._on_video_brightness_update(value)

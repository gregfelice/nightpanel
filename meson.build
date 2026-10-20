project('nightpanel',
  version: '0.3.0',
  meson_version: '>= 0.62.0',
  default_options: ['warning_level=2', 'werror=false'],
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

py_installation = python.find_installation('python3')

dependency('glib-2.0')
dependency('gtk4', version: '>= 4.10')
dependency('libadwaita-1', version: '>= 1.4')

APPLICATION_ID = 'io.github.gregfelice.Nightpanel'
PKGDATA_DIR = get_option('prefix') / get_option('datadir') / meson.project_name()
# Use Python's purelib for module installation (mesonpy compatible)
MODULE_DIR = py_installation.get_install_dir() / 'nightpanel'

conf = configuration_data()
conf.set('APPLICATION_ID', APPLICATION_ID)
conf.set('VERSION', meson.project_version())
conf.set('PKGDATA_DIR', PKGDATA_DIR)
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pythondir', py_installation.get_install_dir())

# Launcher script - configured at root to avoid build dir conflicts with src/nightpanel/
configure_file(
  input: 'src/nightpanel.in',
  output: 'nightpanel',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

# Toggle launcher - gi-free entry point the GNOME panel button and CLI invoke.
configure_file(
  input: 'src/nightpanel-toggle.in',
  output: 'nightpanel-toggle',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

# Player launcher - the standalone theme-synced mini-player (Super+P target).
configure_file(
  input: 'src/nightpanel-player.in',
  output: 'nightpanel-player',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

# GNOME Shell extension - the panel button. Installed system-wide; it resolves
# the toggle binary via PATH at runtime (see extension.js).
install_subdir(
  'src/nightpanel/shell-extension/nightpanel@nightpanel',
  install_dir: get_option('datadir') / 'gnome-shell' / 'extensions'
)

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)

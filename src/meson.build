# Use MODULE_DIR from parent meson.build (purelib path for mesonpy)
moduledir = MODULE_DIR

nightpanel_sources = [
  'nightpanel/__init__.py',
  'nightpanel/__main__.py',
  'nightpanel/application.py',
  'nightpanel/icon_theme.py',
  'nightpanel/window.py',
  'nightpanel/palette.py',
  'nightpanel/player_app.py',
  'nightpanel/toggle.py',
]

install_data(nightpanel_sources, install_dir: moduledir)

subdir('nightpanel/adapters')
subdir('nightpanel/renderers')
subdir('nightpanel/models')
subdir('nightpanel/services')
subdir('nightpanel/views')
subdir('nightpanel/resources')

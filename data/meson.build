desktop_file = i18n.merge_file(
  input: 'io.github.gregfelice.Nightpanel.desktop.in',
  output: 'io.github.gregfelice.Nightpanel.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'applications'
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
  input: 'io.github.gregfelice.Nightpanel.metainfo.xml.in',
  output: 'io.github.gregfelice.Nightpanel.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'metainfo'
)

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('Validate appstream file', appstreamcli,
    args: ['validate', '--no-net', '--explain', appstream_file])
endif

install_data('io.github.gregfelice.Nightpanel.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas'
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test('Validate schema file',
    compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()])
endif

# Binds Super+P to the mini-player once per user, at login. See ensure_player_shortcut().
install_data('nightpanel-shortcuts.desktop',
  install_dir: get_option('sysconfdir') / 'xdg' / 'autostart'
)

subdir('icons')

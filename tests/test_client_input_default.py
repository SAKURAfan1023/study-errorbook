from PIL import Image

from study_errorbook.service import configured_workspace


def test_client_fallback_allows_first_import_without_secret_configuration(tmp_path, monkeypatch):
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    image = inbox / 'synthetic.png'
    Image.new('RGB', (20, 20), 'white').save(image)
    for name, value in {'STUDY_DATA_DIR': str(tmp_path / 'data'), 'STUDY_CONFIG_FILE': str(tmp_path / 'absent.env'), 'STUDY_INPUT_ROOT': '', 'STUDY_CLIENT_INPUT_ROOT': str(inbox), 'TEXTIN_APP_ID': '', 'TEXTIN_SECRET_CODE': ''}.items():
        monkeypatch.setenv(name, value)
    workspace = configured_workspace()
    try:
        assert workspace.import_document(str(image))['pages'] == 1
        assert not workspace.status()['textin_configured']
        assert not (tmp_path / 'absent.env').exists()
    finally:
        workspace.close()


def test_explicit_core_configuration_wins_over_client_fallback(tmp_path, monkeypatch):
    config = tmp_path / 'config.env'
    explicit = tmp_path / 'configured-inbox'
    explicit.mkdir()
    config.write_text('STUDY_INPUT_ROOT=' + str(explicit) + '\n')
    for name in ('STUDY_INPUT_ROOT', 'TEXTIN_APP_ID', 'TEXTIN_SECRET_CODE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('STUDY_CONFIG_FILE', str(config))
    monkeypatch.setenv('STUDY_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('STUDY_CLIENT_INPUT_ROOT', str(tmp_path / 'client-default'))
    workspace = configured_workspace()
    try:
        assert workspace.input_root == explicit
    finally:
        workspace.close()

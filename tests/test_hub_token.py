import stat
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from loader import discover as hub, server
from loader.engines import PROJECT

TOKEN = 'hf_' + 'a1B2c3D4e5' * 4
HEADERS = {'X-Inflect-Local': '1'}


@pytest.fixture
def token_home(monkeypatch, tmp_path):
    for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HF_HOME', str(tmp_path / 'hf-cache'))
    monkeypatch.setattr(hub, 'TOKEN_FILE', tmp_path / 'config' / 'inflect' / 'huggingface-token')
    monkeypatch.setattr(hub, 'whoami', AsyncMock(return_value=dict(user='someone', role='read')))
    return tmp_path


def test_default_token_file_is_outside_the_project():
    assert not hub.TOKEN_FILE.resolve().is_relative_to(PROJECT.resolve())


def test_saved_token_is_private_and_used(token_home):
    assert hub.hub_token() is None and hub.token_source() is None
    hub.save_token(TOKEN)
    assert stat.S_IMODE(hub.TOKEN_FILE.stat().st_mode) == 0o600
    assert hub.hub_token() == TOKEN and hub.token_source() == 'inflect'
    assert hub.hub_headers()['Authorization'] == f'Bearer {TOKEN}'
    hub.remove_token()
    assert hub.hub_token() is None


def test_token_priority(token_home, monkeypatch):
    cli = token_home / 'hf-cache' / 'token'
    cli.parent.mkdir()
    cli.write_text('hf_cli_token_value_1234567890\n')
    assert hub.token_source() == 'huggingface-cli'
    hub.save_token(TOKEN)
    assert hub.hub_token() == TOKEN
    monkeypatch.setenv('HF_TOKEN', 'hf_environment_value_123456789')
    assert hub.hub_token() == 'hf_environment_value_123456789' and hub.token_source() == 'environment'


def test_api_saves_token_but_never_returns_it(token_home):
    client = TestClient(server.app)
    saved = client.post('/api/huggingface', json={'token': TOKEN}, headers=HEADERS)
    assert saved.status_code == 200, saved.text
    assert TOKEN not in saved.text
    assert saved.json()['user'] == 'someone' and saved.json()['masked'].endswith(TOKEN[-4:])
    status = client.get('/api/huggingface', headers=HEADERS)
    assert TOKEN not in status.text and status.json()['connected']
    removed = client.post('/api/huggingface/remove', json={}, headers=HEADERS)
    assert not removed.json()['connected']
    assert not hub.TOKEN_FILE.exists()


def test_rejected_token_is_not_saved(token_home, monkeypatch):
    monkeypatch.setattr(hub, 'whoami', AsyncMock(side_effect=ValueError('Hugging Face did not accept this token.')))
    response = TestClient(server.app).post('/api/huggingface', json={'token': TOKEN}, headers=HEADERS)
    assert response.status_code == 422
    assert not hub.TOKEN_FILE.exists()


def test_malformed_token_gets_a_plain_explanation(token_home):
    response = TestClient(server.app).post('/api/huggingface', json={'token': 'not a token'}, headers=HEADERS)
    assert response.status_code == 422
    assert 'starts with hf_' in response.json()['detail']
    assert not hub.TOKEN_FILE.exists()

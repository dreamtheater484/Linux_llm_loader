import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from loader.conversations import ChatStore
from loader.chat_api import conversation_router


def add_file(store, chat_id, text='private attachment unique payload', **changes):
    values = dict(name='notes.txt', kind='text', mime='text/plain', data=base64.b64encode(text.encode()).decode(), text=text)
    values.update(changes)
    return store.upload(chat_id, **values)


def test_history_drafts_files_survive_reopening_and_search(tmp_path):
    store = ChatStore(tmp_path)
    doc = store.create('exl3', 'A saved conversation')
    file = add_file(store, doc['id'])
    doc = store.update(doc['id'], 0, dict(draft='Pending question', draft_attachments=[file['id']], system_prompt='Be concise.'))
    doc = store.begin(doc['id'], doc['revision'], 'needle in the conversation', [file['id']], dict(id='exl3', title='ExLlama'))
    doc['messages'][-1]['content'] = 'A saved answer.'
    store.save_generation(doc)
    reopened = ChatStore(tmp_path)
    result = reopened.get(doc['id'])
    assert result['messages'][-1]['content'] == 'A saved answer.'
    assert result['system_prompt'] == 'Be concise.'
    assert len(reopened.list('NEEDLE')) == 1
    assert reopened.list('absent') == []
    assert reopened.history(result)[0] == {'role': 'system', 'content': 'Be concise.'}
    assert 'private attachment unique payload' in reopened.history(result)[1]['content']
    assert len(reopened.attachments(doc['id'])) == 1


def test_stale_edits_fail_and_deleted_chats_never_resurrect(tmp_path):
    store = ChatStore(tmp_path)
    doc = store.create()
    changed = store.update(doc['id'], 0, {'draft': 'first'})
    with pytest.raises(HTTPException) as failure:
        store.update(doc['id'], 0, {'draft': 'stale'})
    assert failure.value.status_code == 409
    store.delete(doc['id'])
    with pytest.raises(HTTPException) as failure:
        store.save_generation(changed)
    assert failure.value.status_code == 404
    assert store.list() == []


def test_permanent_delete_scrubs_database_and_cascades_attachments(tmp_path):
    store = ChatStore(tmp_path)
    marker = 'UNIQUE_PRIVATE_CONVERSATION_MARKER_672be317'
    doc = store.create(title=marker)
    file = add_file(store, doc['id'], marker)
    doc = store.update(doc['id'], doc['revision'], dict(draft=marker, draft_attachments=[file['id']]))
    assert marker.encode() in store.path.read_bytes()
    store.delete(doc['id'])
    assert marker.encode() not in store.path.read_bytes()
    assert list(tmp_path.glob('*-wal')) == []
    assert list(tmp_path.glob('*-journal')) == []
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] == 0
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_attachments_cannot_cross_conversation_boundaries(tmp_path):
    store = ChatStore(tmp_path)
    first, second = store.create(), store.create()
    file = add_file(store, first['id'])
    with pytest.raises(HTTPException):
        store.update(second['id'], 0, dict(draft_attachments=[file['id']]))
    with pytest.raises(HTTPException):
        store.attachment(second['id'], file['id'])
    with pytest.raises(HTTPException):
        store.begin(second['id'], 0, 'hi', [file['id']], dict(id='test', title='Test'))


def test_branch_copies_files_and_edit_preserves_original(tmp_path):
    store = ChatStore(tmp_path)
    doc = store.create()
    file = add_file(store, doc['id'])
    doc = store.begin(doc['id'], 0, 'Original prompt', [file['id']], dict(id='test', title='Test'))
    fork = store.fork(doc['id'], 1, 'Edited prompt')
    assert fork['messages'][0]['content'] == 'Edited prompt'
    assert store.get(doc['id'])['messages'][0]['content'] == 'Original prompt'
    assert fork['messages'][0]['attachments'] != doc['messages'][0]['attachments']
    store.delete(doc['id'])
    assert len(store.attachments(fork['id'])) == 1
    assert 'private attachment' in store.history(fork)[0]['content']


class FakeSupervisor:
    def __init__(self, engine='exl3'):
        self.engine = engine
        self.state = 'ready'
        self.model = dict(id=engine, title=engine)
        self.settings = SimpleNamespace(vision=True)
        self.generation_lock = asyncio.Lock()
        self.cancel = asyncio.Event()
        self.last_usage = None
        self.logs = ['engine status']
        self.stopped = False
        self.received = None

    def check_request(self, messages, *args):
        if self.state != 'ready':
            raise HTTPException(422, 'Load a model.')
        if self.generation_lock.locked():
            raise HTTPException(409, 'Busy')

    def snapshot(self):
        return dict(busy=self.generation_lock.locked())

    async def stop(self):
        self.stopped = True
        self.state = 'idle'

    async def stream(self, messages, *args, **kwargs):
        self.received = messages
        async with self.generation_lock:
            yield dict(type='context', input_tokens=12)
            yield dict(type='token', text='Hello ', reasoning='Thinking.')
            yield dict(type='token', text=self.engine, reasoning='')
            yield dict(type='complete', usage=dict(total_tokens=20, completion_tokens=8), tokens_per_second=12, first_token_seconds=.2)


def client_for(tmp_path, engine='exl3'):
    supervisor = FakeSupervisor(engine)
    app = FastAPI()
    app.include_router(conversation_router(lambda: tmp_path, supervisor))
    return TestClient(app), supervisor


@pytest.mark.parametrize('engine', ['exl3', 'gguf'])
def test_generation_uses_shared_engine_and_persists_before_stream_finishes(tmp_path, engine):
    client, supervisor = client_for(tmp_path, engine)
    doc = client.post('/api/conversations', json=dict(model_id=engine)).json()
    response = client.post(f'/api/conversations/{doc["id"]}/generate', json=dict(revision=0, model_id=engine, prompt='Hello', max_output=32))
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[0]['type'] == 'conversation'
    assert events[-1]['type'] == 'saved'
    assert not events[-1]['conversation']['generating']
    saved = client.get(f'/api/conversations/{doc["id"]}').json()
    assert saved['messages'][-1]['content'] == 'Hello ' + engine
    assert saved['messages'][-1]['reasoning'] == 'Thinking.'
    assert saved['used_context'] == 20
    assert supervisor.received == [dict(role='user', content='Hello')]
    deleted = client.delete(f'/api/conversations/{doc["id"]}')
    assert deleted.status_code == 200
    assert supervisor.stopped
    assert supervisor.logs == []
    assert client.get(f'/api/conversations/{doc["id"]}').status_code == 404


def test_unsupported_attachment_rejected_without_losing_draft(tmp_path):
    client, supervisor = client_for(tmp_path)
    doc = client.post('/api/conversations', json={}).json()
    path = '/api/conversations/' + doc['id']
    audio = client.post(path + '/attachments', json=dict(name='sample.wav', kind='audio', mime='audio/wav', data=base64.b64encode(b'RIFF audio').decode())).json()
    saved = client.patch(path, json=dict(revision=0, draft='Listen', draft_attachments=[audio['id']])).json()
    response = client.post(path + '/generate', json=dict(revision=saved['revision'], model_id='exl3', prompt='Listen', attachment_ids=[audio['id']]))
    assert response.status_code == 422
    assert 'transcript' in response.json()['detail']
    assert client.get(path).json()['draft'] == 'Listen'
    assert client.get(path).json()['messages'] == []


def test_html_is_served_as_text_with_sandbox_and_export_roundtrip(tmp_path):
    client, _ = client_for(tmp_path)
    doc = client.post('/api/conversations', json={}).json()
    path = '/api/conversations/' + doc['id']
    body = dict(name='preview.html', kind='html', mime='text/html', data=base64.b64encode(b'<script>alert(1)</script>').decode(), text='<script>alert(1)</script>')
    attached = client.post(path+'/attachments', json=body).json()
    response = client.get(path+'/attachments/'+attached['id'])
    assert response.headers['content-type'].startswith('text/plain')
    assert 'sandbox' in response.headers['content-security-policy']
    assert client.post(path+'/import', json=dict(title='Imported', messages=[dict(role='user', content='Review this', attachments=[attached['id']])])).status_code == 200
    exported = client.get(path+'/export').json()
    assert exported['format'] == 'inflect-chat'
    assert exported['conversation']['messages'][0]['content'] == 'Review this'
    assert exported['attachments'][0]['data'] == body['data']


def test_failed_stream_is_saved_with_partial_text(tmp_path):
    client, supervisor = client_for(tmp_path)
    async def broken(*args, **kwargs):
        yield dict(type='token', text='Partial answer', reasoning='')
        raise RuntimeError('Connection interrupted')
    supervisor.stream = broken
    doc = client.post('/api/conversations', json={}).json()
    path = '/api/conversations/' + doc['id']
    response = client.post(path+'/generate', json=dict(revision=0, model_id='exl3', prompt='Question'))
    assert response.status_code == 200
    saved = client.get(path).json()
    assert saved['messages'][-1]['content'] == 'Partial answer'
    assert saved['messages'][-1]['error']
    assert not saved['generating']


def test_delete_never_interrupts_an_unrelated_request(tmp_path):
    client, supervisor = client_for(tmp_path)
    store = ChatStore(tmp_path)
    doc = store.create()
    store.begin(doc['id'], 0, 'Question', [], supervisor.model)
    supervisor.snapshot = lambda: dict(busy=True)
    response = client.delete('/api/conversations/' + doc['id'])
    assert response.status_code == 409
    assert not supervisor.stopped
    assert store.get(doc['id'])


def test_restart_recovers_interrupted_checkpoint(tmp_path):
    store = ChatStore(tmp_path)
    doc = store.create()
    doc = store.begin(doc['id'], 0, 'Question', [], dict(id='test', title='Test'))
    doc['messages'][-1]['content'] = 'Checkpointed partial answer'
    store.save_generation(doc)
    recovered = ChatStore(tmp_path).get(doc['id'])
    assert recovered['messages'][-1]['content'] == 'Checkpointed partial answer'
    assert recovered['messages'][-1]['cancelled'] is True
    assert recovered['messages'][-1]['pending'] is False


def test_large_native_image_does_not_need_duplicate_upload_payload(tmp_path):
    store = ChatStore(tmp_path)
    doc = store.create()
    data = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'test' * 100).decode()
    attachment = store.upload(doc['id'], 'image.png', 'image', 'image/png', data)
    saved = store.attachment(doc['id'], attachment['id'])
    assert saved['image'] == 'data:image/png;base64,' + data

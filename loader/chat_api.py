"""Conversation endpoints share the qualified inference pipeline with /api/chat."""
import asyncio
import base64
from contextlib import suppress
import json
import secrets
import time
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, ConfigDict

from .conversations import ChatStore
from .reasoning import ReasoningEffort


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchChats(Input):
    query: str = Field('', max_length=300)


class CreateChat(Input):
    model_id: str = Field('', max_length=200)
    title: str = Field('New conversation', min_length=1, max_length=140)


class EditChat(Input):
    revision: int = Field(ge=0)
    title: str | None = Field(None, min_length=1, max_length=140)
    model_id: str | None = Field(None, max_length=200)
    draft: str | None = Field(None, max_length=2_000_000)
    draft_attachments: list[str] | None = Field(None, max_length=20)
    system_prompt: str | None = Field(None, max_length=100_000)


class Upload(Input):
    name: str = Field(min_length=1, max_length=240)
    kind: Literal['image', 'pdf', 'audio', 'text', 'html', 'svg', 'markdown', 'code']
    mime: str = Field(max_length=120)
    data: str = Field(max_length=16_000_000)
    text: str = Field('', max_length=2_000_000)
    image: str | None = Field(None, max_length=16_000_000)


class Generate(Input):
    revision: int = Field(ge=0)
    model_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field('', max_length=2_000_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    retry: bool = False
    max_output: int | None = Field(None, ge=0, le=32768)
    temperature: float | None = Field(None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None


class Fork(Input):
    through: int = Field(ge=0, le=1000)
    edited_prompt: str | None = Field(None, min_length=1, max_length=2_000_000)


class ImportedMessage(Input):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=2_000_000)
    reasoning: str = Field('', max_length=2_000_000)
    attachments: list[str] = Field(default_factory=list, max_length=20)
    model: str = Field('', max_length=300)
    cancelled: bool = False
    error: bool = False
    metrics: dict | None = None


class ImportChat(Input):
    title: str = Field('Imported conversation', min_length=1, max_length=140)
    model_id: str = Field('', max_length=200)
    system_prompt: str = Field('', max_length=100_000)
    messages: list[ImportedMessage] = Field(default_factory=list, max_length=1000)
    draft: str = Field('', max_length=2_000_000)
    draft_attachments: list[str] = Field(default_factory=list, max_length=20)


def conversation_router(state_path, supervisor):
    router = APIRouter(prefix='/api/conversations')
    stores = {}
    active = {}

    def store():
        path = state_path()
        if path not in stores:
            stores[path] = ChatStore(path)
        return stores[path]

    def editable(chat_id):
        if chat_id in active:
            raise HTTPException(409, 'Stop this reply before changing the conversation.')

    def complete(doc):
        return {**doc, 'files': store().attachments(doc['id']), 'generating': doc['id'] in active}

    @router.get('')
    def listing(q: str = Query('', max_length=300)):
        return [{**doc, 'generating': doc['id'] in active} for doc in store().list(q)]

    @router.post('/search')
    def search(body: SearchChats):
        return [{**doc, 'generating': doc['id'] in active} for doc in store().list(body.query)]

    @router.post('', status_code=201)
    def create(body: CreateChat):
        return complete(store().create(body.model_id, body.title))

    @router.get('/{chat_id}')
    def get(chat_id: str):
        return complete(store().get(chat_id))

    @router.patch('/{chat_id}')
    def update(chat_id: str, body: EditChat):
        editable(chat_id)
        changes = body.model_dump(exclude_none=True, exclude={'revision'})
        return complete(store().update(chat_id, body.revision, changes))

    @router.post('/{chat_id}/fork', status_code=201)
    def fork(chat_id: str, body: Fork):
        editable(chat_id)
        return complete(store().fork(chat_id, body.through, body.edited_prompt))

    @router.post('/{chat_id}/import')
    def import_chat(chat_id: str, body: ImportChat):
        editable(chat_id)
        doc = store().get(chat_id)
        if doc['messages']:
            raise HTTPException(409, 'Import into a new empty conversation.')
        file_ids = {f['id'] for f in store().attachments(chat_id)}
        if any(a not in file_ids for m in body.messages for a in m.attachments) or any(a not in file_ids for a in body.draft_attachments):
            raise HTTPException(422, 'Import references a missing attachment.')
        doc.update(body.model_dump())
        doc['messages'] = [{**m, 'id': secrets.token_hex(12), 'created_at': time.time()} for m in doc['messages']]
        return complete(store().save_generation(doc))

    @router.get('/{chat_id}/export')
    def export(chat_id: str):
        editable(chat_id)
        return JSONResponse(store().export(chat_id), headers={'Content-Disposition': f'attachment; filename="inflect-chat-{chat_id[:8]}.json"'})

    @router.post('/{chat_id}/attachments', status_code=201)
    def upload(chat_id: str, body: Upload):
        editable(chat_id)
        if len(store().attachments(chat_id)) >= 200:
            raise HTTPException(422, 'A conversation can contain up to 200 files.')
        return store().upload(chat_id, **body.model_dump())

    @router.get('/{chat_id}/attachments/{file_id}')
    def attachment(chat_id: str, file_id: str, download: bool = False):
        file = store().attachment(chat_id, file_id)
        # Never serve executable HTML or SVG in the application origin.
        safe = {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'application/pdf', 'audio/mpeg', 'audio/wav', 'audio/x-wav'}
        mime = file['mime'] if file['mime'] in safe else 'text/plain; charset=utf-8'
        headers = {'Content-Security-Policy': "sandbox; default-src 'none'; frame-ancestors 'self'"}
        if download:
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(file['name'], safe='')
        return Response(file['data'], media_type=mime, headers=headers)

    @router.delete('/{chat_id}/attachments/{file_id}')
    def remove_attachment(chat_id: str, file_id: str):
        editable(chat_id)
        store().remove_attachment(chat_id, file_id)
        return {'deleted': True}

    @router.delete('/{chat_id}')
    async def delete(chat_id: str):
        editable(chat_id)
        doc = store().get(chat_id)
        had_reply = any(m['role'] == 'assistant' for m in doc['messages'])
        # Both engines retain token/KV caches. Ending the owned process is the
        # portable way to release them, including ExLlama's cached prompt tokens.
        if had_reply and supervisor.state != 'idle':
            if supervisor.generation_lock.locked() or supervisor.snapshot().get('busy'):
                raise HTTPException(409, 'Finish or stop the active request before permanent deletion.')
            await supervisor.stop()
        store().delete(chat_id)
        if had_reply:
            supervisor.last_usage = None
            supervisor.logs.clear()
        return {'deleted': True, 'model_unloaded': had_reply}

    @router.post('/{chat_id}/stop')
    async def stop(chat_id: str):
        task = active.get(chat_id)
        if task:
            supervisor.cancel.set()
            # Cancels token counting as well as decoding. finally persists partial output.
            task.cancel()
        return {'status': 'stopping' if task else 'idle'}

    @router.post('/{chat_id}/generate')
    async def generate(chat_id: str, body: Generate):
        editable(chat_id)
        if active:
            raise HTTPException(409, 'Another conversation is generating. Stop it or wait for completion.')
        if not supervisor.model or supervisor.model['id'] != body.model_id:
            raise HTTPException(409, 'The loaded model changed. Select the loaded model before sending.')
        current = store().get(chat_id)
        # Validate attachment capability and context before consuming the draft.
        candidate = json.loads(json.dumps(current))
        if not body.retry:
            candidate['messages'].append(dict(role='user', content=body.prompt or 'Please review the attached files.', attachments=body.attachment_ids))
        messages = store().history(candidate, supervisor.settings.vision)
        supervisor.check_request(messages, body.max_output, body.reasoning_effort)
        doc = store().begin(chat_id, body.revision, body.prompt, body.attachment_ids, supervisor.model, body.retry)
        # Reserve before returning the response, closing the same-chat double-send race.
        active[chat_id] = asyncio.current_task()

        async def events():
            active[chat_id] = asyncio.current_task()
            assistant = doc['messages'][-1]
            last_save, finished = time.monotonic(), False
            def event(value):
                return 'data: ' + json.dumps(value) + '\n\n'
            try:
                yield event({'type': 'conversation', 'conversation': complete(doc)})
                async for item in supervisor.stream(messages, body.max_output, body.temperature, reasoning_effort=body.reasoning_effort):
                    if item['type'] == 'token':
                        assistant['content'] += item.get('text', '')
                        assistant['reasoning'] += item.get('reasoning', '')
                    elif item['type'] == 'context':
                        doc['used_context'] = item['input_tokens']
                    elif item['type'] == 'complete':
                        assistant['metrics'] = item
                        doc['used_context'] = item['usage'].get('total_tokens')
                        finished = True
                    elif item['type'] == 'cancelled':
                        assistant['cancelled'] = True
                        finished = True
                    if time.monotonic() - last_save >= 1:
                        store().save_generation(doc)
                        last_save = time.monotonic()
                    yield event(item)
            except asyncio.CancelledError:
                assistant['cancelled'] = True
            except Exception as exc:
                assistant['error'] = True
                assistant['error_message'] = str(exc)
                yield event({'type': 'error', 'message': str(exc)})
            finally:
                assistant['pending'] = False
                if not finished and not assistant.get('error'):
                    assistant['cancelled'] = True
                with suppress(HTTPException):
                    store().save_generation(doc)
                active.pop(chat_id, None)
            yield event({'type': 'saved', 'conversation': complete(doc)})

        def release_unstarted():
            # A disconnect may arrive before Starlette starts iterating events.
            # Its response background hook still releases that reservation.
            if chat_id in active:
                doc['messages'][-1].update(pending=False, cancelled=True)
                with suppress(HTTPException):
                    store().save_generation(doc)
                active.pop(chat_id, None)

        return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'}, background=BackgroundTask(release_unstarted))

    return router

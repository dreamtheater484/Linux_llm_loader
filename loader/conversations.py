"""Private conversation storage. No trash, browser copies, FTS shadow tables or WAL.

secure_delete scrubs SQLite pages; DELETE journals are removed on commit. This is
application-level erasure, not a guarantee about SSD firmware or external backups.
"""
import base64
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import HTTPException

MAX_FILE = 12_000_000
MAX_TEXT = 2_000_000


class ChatStore:
    def __init__(self, state):
        self.path = Path(state) / 'conversations.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, updated REAL NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, kind TEXT NOT NULL, mime TEXT NOT NULL,
                    data BLOB NOT NULL, text TEXT NOT NULL, image TEXT
                );
                CREATE INDEX IF NOT EXISTS attachment_chat ON attachments(conversation_id);
            ''')
        self.path.chmod(0o600)
        # A manager crash can leave a checkpointed reply in progress. Preserve
        # its text and make its interrupted state explicit after restart.
        with self.connect() as db:
            for row in db.execute('SELECT document FROM conversations').fetchall():
                doc = json.loads(row[0])
                if doc['messages'] and doc['messages'][-1].get('pending'):
                    doc['messages'][-1].update(pending=False, cancelled=True)
                    self._write(db, doc)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA temp_store=MEMORY')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, chat_id):
        row = db.execute('SELECT document FROM conversations WHERE id=?', (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Conversation no longer exists.')
        return json.loads(row[0])

    def _write(self, db, doc):
        doc['updated_at'] = time.time()
        doc['revision'] += 1
        db.execute('UPDATE conversations SET document=?, updated=? WHERE id=?',
                   (json.dumps(doc, ensure_ascii=False), doc['updated_at'], doc['id']))
        return doc

    def get(self, chat_id):
        with self.connect() as db:
            return self._get(db, chat_id)

    def list(self, query=''):
        with self.connect() as db:
            docs = [json.loads(row[0]) for row in db.execute('SELECT document FROM conversations ORDER BY updated DESC')]
        needle = query.casefold().strip()
        result = []
        for doc in docs:
            if needle and needle not in (doc['title'] + '\n' + '\n'.join(m['content'] for m in doc['messages'])).casefold():
                continue
            result.append({k: doc[k] for k in ('id', 'title', 'model_id', 'created_at', 'updated_at', 'revision')} |
                          {'message_count': len(doc['messages']), 'preview': next((m['content'][:120] for m in reversed(doc['messages']) if m['content']), '')})
        return result

    def create(self, model_id='', title='New conversation'):
        now = time.time()
        doc = dict(id=secrets.token_hex(16), title=title, model_id=model_id, created_at=now, updated_at=now,
                   revision=0, messages=[], draft='', draft_attachments=[], system_prompt='', used_context=None)
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES (?, ?, ?)', (doc['id'], now, json.dumps(doc)))
        return doc

    def update(self, chat_id, revision, changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            doc = self._get(db, chat_id)
            if doc['revision'] != revision:
                raise HTTPException(409, 'This chat changed in another window. Reopen it before editing.')
            for key in ('title', 'model_id', 'draft', 'draft_attachments', 'system_prompt'):
                if key in changes:
                    doc[key] = changes[key]
            self._check_attachments(db, chat_id, doc['draft_attachments'])
            return self._write(db, doc)

    def _check_attachments(self, db, chat_id, ids):
        for file_id in ids:
            if not db.execute('SELECT 1 FROM attachments WHERE id=? AND conversation_id=?', (file_id, chat_id)).fetchone():
                raise HTTPException(422, 'An attachment does not belong to this conversation.')

    def save_generation(self, doc):
        with self.connect() as db:
            # UPDATE only: a late stream must never recreate a deleted conversation.
            current = self._get(db, doc['id'])
            doc['revision'] = current['revision']
            return self._write(db, doc)

    def begin(self, chat_id, revision, prompt, attachment_ids, model, retry=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            doc = self._get(db, chat_id)
            if revision != doc['revision']:
                raise HTTPException(409, 'This chat changed. Reopen it and try again.')
            self._check_attachments(db, chat_id, attachment_ids)
            if retry:
                if not doc['messages'] or doc['messages'][-1]['role'] != 'user':
                    raise HTTPException(422, 'Retry requires a conversation ending in a user message.')
            else:
                if not prompt.strip() and not attachment_ids:
                    raise HTTPException(422, 'Write a message or attach a file.')
                doc['messages'].append(dict(id=secrets.token_hex(12), role='user', content=prompt.strip() or 'Please review the attached files.',
                                            attachments=attachment_ids, created_at=time.time()))
            if len(doc['messages']) >= 1000:
                raise HTTPException(422, 'This conversation has reached 1,000 messages. Start a new chat.')
            if doc['title'] == 'New conversation':
                doc['title'] = (doc['messages'][0]['content'].splitlines()[0][:70] or 'Conversation')
            doc.update(model_id=model['id'], draft='', draft_attachments=[])
            doc['messages'].append(dict(id=secrets.token_hex(12), role='assistant', content='', reasoning='', attachments=[],
                                        model=model['title'], created_at=time.time(), cancelled=False, pending=True))
            return self._write(db, doc)

    def history(self, doc, vision=False):
        result = []
        if doc['system_prompt'].strip():
            result.append({'role': 'system', 'content': doc['system_prompt']})
        with self.connect() as db:
            for message in doc['messages']:
                if message.get('error') or message['role'] == 'assistant' and not message['content'] and not message.get('reasoning'):
                    continue
                content = message['content']
                images = []
                for file_id in message.get('attachments', []):
                    row = db.execute('SELECT * FROM attachments WHERE id=? AND conversation_id=?', (file_id, doc['id'])).fetchone()
                    if row is None:
                        raise HTTPException(422, 'An attachment is missing. Remove it before sending.')
                    if row['kind'] == 'audio':
                        raise HTTPException(422, 'Audio playback is available, but this engine has no qualified audio input. Attach a transcript instead.')
                    if row['kind'] == 'image':
                        if not vision:
                            raise HTTPException(422, 'This conversation includes images. Load a vision model with its vision tower enabled.')
                        images.append({'type': 'image_url', 'image_url': {'url': row['image']}})
                    elif row['text'].strip():
                        content += f'\n\n<attached_file name={json.dumps(row["name"])}>\n{row["text"]}\n</attached_file>'
                    elif row['kind'] == 'pdf':
                        raise HTTPException(422, 'This PDF has no readable text. Attach page images to a vision model, or use a text-based PDF.')
                item = dict(role=message['role'], content=[{'type': 'text', 'text': content}, *images] if images else content)
                if message.get('reasoning'):
                    item['reasoning_content'] = message['reasoning']
                result.append(item)
        return result

    def upload(self, chat_id, name, kind, mime, data, text='', image=None):
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            raise HTTPException(422, 'Invalid file encoding.')
        if not raw or len(raw) > MAX_FILE:
            raise HTTPException(413, 'Files must be between 1 byte and 12 MB.')
        if len(text) > MAX_TEXT:
            raise HTTPException(413, 'Extracted text exceeds 2 million characters.')
        if kind == 'image':
            if image is None and mime in ('image/png', 'image/jpeg', 'image/webp'):
                image = f'data:{mime};base64,{data}'
            if not image or not image.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')) or len(image) > 16_000_000:
                raise HTTPException(422, 'Image input requires a PNG, JPEG or WebP conversion under 12 MB.')
            try:
                base64.b64decode(image.split(',', 1)[1], validate=True)
            except ValueError:
                raise HTTPException(422, 'Invalid image encoding.')
        file_id = secrets.token_hex(16)
        with self.connect() as db:
            self._get(db, chat_id)
            db.execute('INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?)',
                       (file_id, chat_id, name, kind, mime, raw, text, image))
        return dict(id=file_id, name=name, kind=kind, mime=mime, size=len(raw))

    def attachments(self, chat_id):
        with self.connect() as db:
            self._get(db, chat_id)
            return [dict(row) for row in db.execute('SELECT id,name,kind,mime,length(data) AS size FROM attachments WHERE conversation_id=?', (chat_id,))]

    def attachment(self, chat_id, file_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM attachments WHERE id=? AND conversation_id=?', (file_id, chat_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Attachment no longer exists.')
            return dict(row)

    def remove_attachment(self, chat_id, file_id):
        with self.connect() as db:
            doc = self._get(db, chat_id)
            used = doc['draft_attachments'] + [a for m in doc['messages'] for a in m.get('attachments', [])]
            if file_id in used:
                raise HTTPException(409, 'Remove this file from the draft before deleting it.')
            db.execute('DELETE FROM attachments WHERE id=? AND conversation_id=?', (file_id, chat_id))

    def fork(self, chat_id, through, edited_prompt=None):
        source = self.get(chat_id)
        if through < 0 or through > len(source['messages']):
            raise HTTPException(422, 'Invalid branch position.')
        if edited_prompt is not None and (through == 0 or source['messages'][through - 1]['role'] != 'user'):
            raise HTTPException(422, 'Only user messages can be edited.')
        doc = self.create(source['model_id'], source['title'][:65] + ' · branch')
        try:
            doc.update(messages=copy.deepcopy(source['messages'][:through]), system_prompt=source['system_prompt'])
            if edited_prompt is not None:
                doc['messages'][-1]['content'] = edited_prompt
            mapping = {}
            for message in doc['messages']:
                for file_id in message.get('attachments', []):
                    if file_id not in mapping:
                        row = self.attachment(chat_id, file_id)
                        attached = self.upload(doc['id'], row['name'], row['kind'], row['mime'], base64.b64encode(row['data']).decode(), row['text'], row['image'])
                        mapping[file_id] = attached['id']
                message['attachments'] = [mapping[a] for a in message.get('attachments', [])]
            return self.save_generation(doc)
        except BaseException:
            self.delete(doc['id'])
            raise

    def export(self, chat_id):
        doc = self.get(chat_id)
        attachments = []
        for item in self.attachments(chat_id):
            row = self.attachment(chat_id, item['id'])
            attachments.append({**item, 'data': base64.b64encode(row['data']).decode(), 'text': row['text'], 'image': row['image']})
        return dict(format='inflect-chat', version=1, conversation=doc, attachments=attachments)

    def delete(self, chat_id):
        with self.connect() as db:
            self._get(db, chat_id)
            db.execute('DELETE FROM conversations WHERE id=?', (chat_id,))

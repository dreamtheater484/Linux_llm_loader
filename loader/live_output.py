"""A bounded, ephemeral benchmark tail. Model text is never interpreted as HTML."""
from collections import deque
import time


class LiveOutput:
    def __init__(self):
        self.meta = None
        self.blocks = deque(maxlen=256)
        self.sequence = 0
        self.block_id = 0

    def start(self, run_id, kind, title, model):
        self.blocks.clear()
        self.sequence = self.block_id = 0
        self.meta = dict(id=run_id, kind=kind, title=title, model=model, state='running')
        self.append('status', f'{title} started · {model["name"]}\n')

    def append(self, kind, text):
        if not self.meta or not text:
            return
        # Coalesce streaming tokens; retain at most 1,048,576 characters even during long agent runs.
        for offset in range(0, len(text), 4096):
            part = text[offset:offset + 4096]
            self.sequence += 1
            if self.blocks and self.blocks[-1]['kind'] == kind and len(self.blocks[-1]['text']) + len(part) <= 4096:
                self.blocks[-1].update(text=self.blocks[-1]['text'] + part, revision=self.sequence)
            else:
                self.block_id += 1
                self.blocks.append(dict(id=self.block_id, revision=self.sequence, kind=kind,
                                        text=part, timestamp=time.time()))

    def finish(self, result):
        if not self.meta or result['id'] != self.meta['id']:
            return
        self.meta.update(state=result['state'])
        self.append('status', f'\nBenchmark {result["state"]}. {result.get("error", "")}\n')
        if result['state'] in ('cancelled', 'interrupted', 'aborted'):
            # Aborted output is never kept in the archive or in the tail buffer.
            self.blocks.clear()

    def read(self, after=0, run_id=''):
        reset = not self.meta or run_id != self.meta['id'] or after > self.sequence
        return dict(run=self.meta, cursor=self.sequence, reset=reset,
                    first_id=self.blocks[0]['id'] if self.blocks else self.block_id + 1,
                    truncated=bool(self.blocks and self.blocks[0]['id'] > 1),
                    blocks=[dict(b) for b in self.blocks if reset or b['revision'] > after])

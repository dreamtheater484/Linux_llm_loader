"""Find models on Hugging Face that the installed engines can actually run.

Support is decided by what is installed right now: llama.cpp's own table of
GGUF architectures and ExLlamaV3's registered model classes. Nothing here
downloads weights; see downloads.py for that."""
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import statistics
import time

import httpx

from . import engines
from .naming import active_billions, display, family_key, merge_families, quant_label, split_name

HUB = 'https://huggingface.co'
CACHE_SECONDS = 600
PROJECTOR_PREFERENCE = ('f16', 'bf16', 'q8', 'f32')


# Inflect keeps its own copy of a Hugging Face token outside the project folder, so
# it can never be committed with the code. The standard huggingface-cli login and
# the HF_TOKEN environment variable are honoured too.
TOKEN_FILE = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'inflect' / 'huggingface-token'


def _cli_token_file():
    return Path(os.environ.get('HF_HOME', Path.home() / '.cache/huggingface')).expanduser() / 'token'


def token_source():
    """Where the active token comes from: 'environment', 'inflect', 'huggingface-cli' or None."""
    for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        if os.environ.get(key, '').strip():
            return 'environment'
    for source, path in (('inflect', TOKEN_FILE), ('huggingface-cli', _cli_token_file())):
        try:
            if path.read_text(encoding='utf-8').strip():
                return source
        except OSError:
            pass
    return None


def hub_token():
    for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        if os.environ.get(key, '').strip():
            return os.environ[key].strip()
    for path in (TOKEN_FILE, _cli_token_file()):
        try:
            token = path.read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if token:
            return token
    return None


def save_token(token):
    """Store the token readable only by this user (0600), never inside the project."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = TOKEN_FILE.with_name(TOKEN_FILE.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(token.strip() + '\n')
    os.chmod(temporary, 0o600)
    os.replace(temporary, TOKEN_FILE)


def remove_token():
    TOKEN_FILE.unlink(missing_ok=True)


def mask_token(token):
    return f'{token[:3]}…{token[-4:]}' if token and len(token) > 10 else '…'


async def whoami(token):
    """Check a token with Hugging Face. Returns the account name and token role."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=10)) as client:
        response = await client.get(f'{HUB}/api/whoami-v2', headers={'Authorization': f'Bearer {token}',
                                                                      'User-Agent': 'Inflect local model workbench'})
    if response.status_code == 401:
        raise ValueError('Hugging Face did not accept this token. Check that it was copied completely and has not been revoked.')
    response.raise_for_status()
    data = response.json()
    access = (data.get('auth') or {}).get('accessToken') or {}
    return dict(user=data.get('name'), role=access.get('role'))


def hub_headers():
    headers = {'User-Agent': 'Inflect local model workbench'}
    token = hub_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


_support_cache = {}


def _cached(key, stamp, build):
    hit = _support_cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    value = build()
    _support_cache[key] = (stamp, value)
    return value


def llama_architectures(server=None):
    """Read the architecture name table compiled into libllama."""
    server = Path(server or engines.GGUF)
    libraries = sorted(server.parent.glob('libllama.so*')) or sorted(server.parent.parent.glob('lib*/libllama.so*'))
    if not libraries:
        return None
    library = libraries[0]
    try:
        stamp = (str(library), library.stat().st_mtime_ns)
    except OSError:
        return None

    def build():
        data = library.read_bytes()
        start = data.find(b'\x00clip\x00llama\x00')
        if start < 0:
            return None
        names = set()
        for token in data[start + 1:start + 32768].split(b'\x00'):
            text = token.decode('ascii', 'ignore')
            if not re.fullmatch(r'[a-z][a-z0-9_.\-]{1,40}', text):
                break
            names.add(text)
        names.discard('clip')
        return names if len(names) > 20 else None
    return _cached('gguf', stamp, build)


def exl3_architectures(python=None):
    python = Path(python or engines.EXL_PYTHON)
    folders = sorted(python.parent.parent.glob('lib/python*/site-packages/exllamav3/architecture'))
    if not folders:
        return None
    folder = folders[0]
    files = sorted(folder.glob('*.py'))
    stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)

    def build():
        names = set()
        for file in files:
            names.update(re.findall(r'arch_string\s*=\s*["\']([A-Za-z0-9_]+)["\']', file.read_text(encoding='utf-8', errors='ignore')))
        return names or None
    return _cached('exl3', stamp, build)


def engine_support(inventory=None):
    """What can be discovered, based on the engines installed right now."""
    inventory = inventory or engines.engine_inventory()
    result = []
    for engine in inventory:
        architectures = None
        if engine['installed'] and engine['id'] == 'gguf':
            architectures = llama_architectures()
        elif engine['installed'] and engine['id'] == 'exl3':
            architectures = exl3_architectures()
        result.append(dict(id=engine['id'], name=engine['name'], installed=bool(engine['installed']),
                           formats=engine.get('formats', []), version=engine.get('version'),
                           discoverable=bool(engine['installed']) and engine['id'] in ('gguf', 'exl3'),
                           architectures=sorted(architectures) if architectures else None))
    return result


def safe_folder(text):
    return re.sub(r'[^A-Za-z0-9._+-]+', '-', text).strip('-.')[:150] or 'model'


def moe_split(config):
    """(expert parameters, total parameters) estimated from a config.json."""
    text = config.get('text_config', config) if isinstance(config, dict) else {}
    experts = text.get('num_experts') or text.get('n_routed_experts') or text.get('num_local_experts') or 0
    hidden = text.get('hidden_size') or 0
    inner = text.get('moe_intermediate_size') or text.get('intermediate_size') or 0
    layers = text.get('num_hidden_layers') or 0
    if not (experts and hidden and inner and layers):
        return 0
    dense = text.get('first_k_dense_replace') or 0
    step = text.get('decoder_sparse_step') or 1
    moe_layers = max(0, (layers - dense)) // max(1, step)
    return moe_layers * experts * 3 * hidden * inner


def config_facts(config):
    if not isinstance(config, dict):
        return {}
    text = config.get('text_config', config)
    return dict(context=text.get('max_position_embeddings') or config.get('max_position_embeddings'),
                vision=bool(config.get('vision_config')), expert_params=moe_split(config),
                mtp=bool(text.get('mtp_num_hidden_layers') or text.get('num_nextn_predict_layers')))


def _base_models(item):
    base = (item.get('cardData') or {}).get('base_model')
    if isinstance(base, str):
        return [base]
    return [b for b in base or [] if isinstance(b, str)]


class Discovery:
    def __init__(self):
        self.cache = {}
        self.options = {}
        self.client = None

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    def _client(self):
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=HUB, timeout=httpx.Timeout(25, connect=10),
                                            follow_redirects=True, headers=hub_headers())
        return self.client

    async def get(self, path, params=None, ttl=CACHE_SECONDS, optional=False):
        key = (path, json.dumps(params, sort_keys=True, default=str))
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            response = await self._client().get(path, params=params)
        except httpx.HTTPError as exc:
            if optional:
                return None
            raise ValueError('Hugging Face could not be reached. Check your internet connection and try again.') from exc
        if response.status_code in (401, 403, 404) and optional:
            return None
        if response.status_code == 429:
            raise ValueError('Hugging Face is rate limiting requests. Wait a minute and try again.')
        if response.is_error:
            if optional:
                return None
            raise ValueError(f'Hugging Face returned HTTP {response.status_code}. Try again shortly.')
        data = response.json()
        self.cache[key] = (time.time(), data)
        if len(self.cache) > 800:
            for stale in sorted(self.cache, key=lambda k: self.cache[k][0])[:200]:
                self.cache.pop(stale, None)
        return data

    async def list_repos(self, support, query, limit=100):
        enabled = {e['id']: e for e in support if e['discoverable']}
        sort = 'downloads' if query else 'trendingScore'
        requests = []
        if 'gguf' in enabled:
            requests.append(('gguf', dict(search=query, filter='gguf')))
        if 'exl3' in enabled:
            requests.append(('exl3', dict(search=query, filter='exl3')))
            requests.append(('exl3', dict(search=(query + ' exl3').strip())))
        expand = ['downloads', 'likes', 'gated', 'lastModified', 'cardData', 'tags']

        async def fetch(kind, params):
            extra = ['gguf'] if kind == 'gguf' else ['config']
            query_params = [(k, v) for k, v in params.items() if v] + [('sort', sort), ('direction', '-1'), ('limit', str(limit))]
            query_params += [('expand[]', field) for field in expand + extra]
            return kind, await self.get('/api/models', query_params)
        results = await asyncio.gather(*(fetch(kind, params) for kind, params in requests))
        repos = {}
        for kind, items in results:
            for item in items or []:
                entry = self.repo_entry(item, kind, enabled[kind])
                if entry and entry['repo'] not in repos:
                    repos[entry['repo']] = entry
        return list(repos.values())

    def repo_entry(self, item, kind, engine):
        repo = item.get('id', '')
        if '/' not in repo:
            return None
        owner, name = repo.split('/', 1)
        tags = item.get('tags') or []
        # Speculative-decoding drafters are helpers, not models to chat with.
        if re.search(r'(?i)dflash|drafter|dspark|eagle3?(?:$|[-_])|(?:^|[-_])draft(?:$|[-_])', name):
            return None
        if kind == 'gguf':
            gguf = item.get('gguf') or {}
            architecture, context, params = gguf.get('architecture'), gguf.get('context_length'), gguf.get('total')
            if architecture in ('clip', 'mmproj') or 'gguf' not in tags and not name.lower().endswith('gguf'):
                return None
        else:
            config = item.get('config') or {}
            method = (config.get('quantization_config') or {}).get('quant_method')
            if method not in (None, 'exl3') or (method is None and not re.search(r'(?i)exl3', name)):
                return None
            if re.search(r'(?i)\bmlx\b|gguf|exl2', name):
                return None
            architecture = (config.get('architectures') or [None])[0]
            context = params = None
            if architecture and re.search(r'(?i)draft', architecture):
                return None
        known = engine.get('architectures')
        if known and architecture and architecture not in known:
            return None
        family, variant = split_name(name)
        return dict(repo=repo, owner=owner, name=name, engine=kind, format='GGUF' if kind == 'gguf' else 'EXL3',
                    architecture=architecture, context=context, params=params,
                    downloads=item.get('downloads') or 0, likes=item.get('likes') or 0,
                    gated=bool(item.get('gated')), updated=item.get('lastModified'),
                    base_models=_base_models(item), family=family, variant=variant)

    @staticmethod
    def group(repos):
        merged = merge_families({family_key(r['family']) for r in repos})
        families = {}
        for repo in repos:
            key, extra = merged[family_key(repo['family'])]
            repo['family_key'] = key
            if extra:
                repo['variant'] = (extra + ' ' + repo['variant']).strip()
            families.setdefault(key, []).append(repo)
        result = []
        for key, members in families.items():
            names = [m['family'] for m in members if family_key(m['family']) == key] or [key]
            title = max(set(names), key=names.count)
            params = [m['params'] for m in members if m.get('params')]
            contexts = [m['context'] for m in members if m.get('context')]
            owners = {}
            for member in members:
                owners[member['owner']] = owners.get(member['owner'], 0) + member['downloads']
            result.append(dict(key=key, name=title, title=display(title),
                               params=statistics.median(params) if params else None,
                               active_b=active_billions(title),
                               context=max(contexts) if contexts else None,
                               engines=sorted({m['engine'] for m in members}),
                               repos=len(members), downloads=sum(m['downloads'] for m in members),
                               owners=[o for o, _ in sorted(owners.items(), key=lambda kv: -kv[1])][:4],
                               variants=len({m['variant'] for m in members})))
        result.sort(key=lambda f: -f['downloads'])
        return result, families

    async def search(self, support, query=''):
        query = query.strip()[:120]
        repos = await self.list_repos(support, query)
        families, _ = self.group(repos)
        return dict(query=query, families=families[:48], engines=support)

    async def revision(self, repo, revision='main'):
        return await self.get(f'/api/models/{repo}/revision/{revision}', {'blobs': 'true'}, optional=True)

    async def config(self, repo, commit):
        return await self.get(f'/{repo}/resolve/{commit}/config.json', optional=True)

    def gguf_options(self, repo, info):
        siblings = info.get('siblings') or []
        projectors = [s for s in siblings if s['rfilename'].lower().endswith('.gguf') and 'mmproj' in s['rfilename'].lower()]
        projector = None
        for preference in PROJECTOR_PREFERENCE:
            projector = next((p for p in projectors if preference in PurePosixPath(p['rfilename']).stem.lower().split('mmproj')[-1]), None)
            if projector:
                break
        projector = projector or (projectors[0] if projectors else None)
        groups = {}
        for sibling in siblings:
            path = sibling['rfilename']
            lower = path.lower()
            if not lower.endswith('.gguf') or 'mmproj' in lower or 'imatrix' in lower or sibling.get('size') is None:
                continue
            stem = re.sub(r'-\d{5}-of-\d{5}\.gguf$', '', path, flags=re.I)
            stem = re.sub(r'\.gguf$', '', stem, flags=re.I)
            groups.setdefault(stem, []).append(sibling)
        options = []
        names = [PurePosixPath(stem).name for stem in groups]
        common = os.path.commonprefix(names) if len(names) > 1 else ''
        common = common[:common.rfind('-') + 1] if '-' in common else ''
        for stem, files in groups.items():
            name = PurePosixPath(stem).name
            folder = PurePosixPath(stem).parent.name
            fallback = name[len(common):] if common and len(name) > len(common) else re.sub(r'^.*?\d+(?:\.\d+)?B(?:-A\d+(?:\.\d+)?B)?-?', '', name)
            quant = quant_label(name) or quant_label(stem.replace('/', '-')) or (folder if folder not in ('', '.') else fallback) or 'Standard'
            chosen = sorted(files, key=lambda f: f['rfilename'])
            if projector:
                chosen = chosen + [projector]
            options.append(dict(quant=quant, files=chosen, vision=bool(projector), file_stem=name,
                                weight_bytes=sum(f['size'] for f in files)))
        return options

    def exl3_option(self, info, revision):
        siblings = [s for s in info.get('siblings') or [] if s.get('size') is not None]
        names = {s['rfilename'] for s in siblings}
        if 'config.json' not in names or not any(n.endswith('.safetensors') for n in names):
            return None
        quant = (info.get('config') or {}).get('quantization_config') or {}
        bits = quant.get('bits')
        label = revision if revision != 'main' and re.search(r'\d', revision) else (f'{bits:g}bpw' if isinstance(bits, (int, float)) else quant_label(info.get('id', '')) or 'EXL3')
        keep = [s for s in siblings if not s['rfilename'].startswith('.') and not s['rfilename'].lower().endswith(('.md', '.png', '.jpg', '.gif'))]
        return dict(quant=label, files=keep, vision='preprocessor_config.json' in names,
                    weight_bytes=sum(s['size'] for s in keep if s['rfilename'].endswith('.safetensors')))

    async def repo_options(self, repo_entry):
        repo = repo_entry['repo']
        options = []
        if repo_entry['engine'] == 'gguf':
            info = await self.revision(repo)
            if info:
                for option in self.gguf_options(repo, info):
                    options.append((info, 'main', option))
        else:
            refs = await self.get(f'/api/models/{repo}/refs', optional=True) or {}
            branches = [b['name'] for b in refs.get('branches', []) if b.get('name')]
            revisions = [b for b in branches if b != 'main'][:10] + ['main']
            infos = await asyncio.gather(*(self.revision(repo, rev) for rev in revisions))
            for revision, info in zip(revisions, infos):
                option = self.exl3_option(info, revision) if info else None
                if option:
                    options.append((info, revision, option))
        return options

    async def family(self, support, key, name):
        repos = await self.list_repos(support, name.replace(' ', '-'))
        _, families = self.group(repos)
        members = families.get(key) or []
        if not members:
            raise ValueError('This model is no longer listed. Search again.')
        members.sort(key=lambda m: -m['downloads'])
        chosen = [m for m in members if m['engine'] == 'gguf'][:24] + [m for m in members if m['engine'] == 'exl3'][:14]
        semaphore = asyncio.Semaphore(8)

        async def limited(member):
            async with semaphore:
                return await self.repo_options(member)
        resolved = await asyncio.gather(*(limited(m) for m in chosen), return_exceptions=True)
        options, facts = [], {}
        config_source = None
        titles = [m['family'] for m in members if family_key(m['family']) == key] or [name]
        family_name = max(set(titles), key=titles.count)
        for member, found in zip(chosen, resolved):
            if isinstance(found, Exception):
                continue
            for info, revision, option in found:
                commit = info.get('sha')
                if not commit or not re.fullmatch(r'[0-9a-f]{40}', commit):
                    continue
                if member['engine'] == 'exl3' and not config_source:
                    config_source = (member['repo'], commit)
                if member['engine'] == 'gguf' and not member.get('architecture'):
                    member['architecture'] = (info.get('gguf') or {}).get('architecture')
                total = sum(f['size'] for f in option['files'])
                folder_bits = [family_name] + ([member['variant'].replace(' ', '-')] if member['variant'] else [])
                folder_bits += ['EXL3', option['quant']] if member['engine'] == 'exl3' else [option['quant']]
                identity = hashlib.sha256(f"{member['repo']}@{commit}:{option['quant']}:{option.get('file_stem','')}".encode()).hexdigest()[:16]
                record = dict(id=identity, repo=member['repo'], owner=member['owner'], variant=member['variant'] or 'Original',
                              engine=member['engine'], format=member['format'], quant=option['quant'],
                              revision=revision, commit=commit, size=total, weight_bytes=option['weight_bytes'],
                              vision=option['vision'], mtp=bool(re.search(r'(?i)(?:^|[-_ ])mtp(?:$|[-_ ])', member['name'])),
                              gated=member['gated'], downloads=member['downloads'], architecture=member.get('architecture'),
                              files=[dict(path=f['rfilename'], size=f['size'], sha256=(f.get('lfs') or {}).get('sha256'),
                                          blob=f.get('blobId')) for f in option['files']],
                              folder=safe_folder('-'.join(folder_bits + [member['owner']])),
                              title=f"{display(member['family'])} · {option['quant']}", family=key)
                self.options[identity] = (time.time(), record)
                options.append({k: v for k, v in record.items() if k != 'files'} | {'file_count': len(record['files'])})
        # Facts: prefer a full config.json (EXL3 repos ship one); fall back to GGUF metadata.
        base = next((b for m in members for b in m['base_models']), None)
        config = await self.config(*config_source) if config_source else None
        if config is None and base:
            config = await self.config(base, 'main')
        facts = config_facts(config)
        params = [m['params'] for m in members if m.get('params')]
        total_params = statistics.median(params) if params else None
        contexts = [m['context'] for m in members if m.get('context')]
        context = facts.get('context') or (max(contexts) if contexts else None)
        expert_fraction = None
        if facts.get('expert_params') and total_params:
            expert_fraction = min(.97, facts['expert_params'] / total_params)
        elif active_billions(name) and total_params:
            expert_fraction = max(0, 1 - active_billions(name) * 1e9 / total_params)
        elif facts.get('expert_params') is not None and not facts.get('expert_params') and config:
            expert_fraction = 0
        now = time.time()
        for stale in [k for k, (t, _) in self.options.items() if now - t > 3 * CACHE_SECONDS]:
            self.options.pop(stale, None)
        return dict(key=key, name=family_name, title=display(family_name), base_model=base,
                    params=total_params, active_b=active_billions(family_name),
                    expert_fraction=expert_fraction, context=context,
                    vision=facts.get('vision') or any(o['vision'] for o in options),
                    engines=sorted({o['engine'] for o in options}), options=options,
                    architectures=sorted({m['architecture'] for m in members if m.get('architecture')}))

    def option(self, identity):
        hit = self.options.get(identity)
        if not hit or time.time() - hit[0] > 3 * CACHE_SECONDS:
            raise ValueError('This download option has expired. Open the model again to refresh it.')
        return hit[1]

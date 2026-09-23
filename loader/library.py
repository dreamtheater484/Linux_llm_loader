"""Inspect model metadata without importing model code or loading tensor data."""
import hashlib
import json
from pathlib import Path
import re
import struct

from .reasoning import inspect_reasoning, native_reasoning
from .naming import active_billions, split_name


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('A model file points outside its model directory.')
    return path


def gguf_metadata(path, tensors=False):
    """Read selected GGUF fields, skipping token arrays and tensor payloads.

    With tensors=True, also sum the bytes of routed-expert tensors from the
    tensor directory, so memory placement can be estimated before loading."""
    file_size = path.stat().st_size
    with path.open('rb') as f:
        def unpack(fmt):
            size = struct.calcsize(fmt)
            data = f.read(size)
            if len(data) != size:
                raise ValueError('Truncated GGUF header')
            return struct.unpack(fmt, data)[0]

        def string(keep=True):
            size = unpack('<Q')
            if size > file_size - f.tell():
                raise ValueError('Invalid GGUF string length')
            if keep:
                if size > 1_000_000:
                    raise ValueError('Oversized metadata string')
                return f.read(size).decode('utf-8', errors='replace')
            f.seek(size, 1)

        def value(kind, keep, depth=0):
            formats = {0:'<B', 1:'<b', 2:'<H', 3:'<h', 4:'<I', 5:'<i', 6:'<f', 7:'<?', 10:'<Q', 11:'<q', 12:'<d'}
            if kind in formats:
                return unpack(formats[kind])
            if kind == 8:
                return string(keep)
            if kind == 9 and depth < 3:
                subtype, length = unpack('<I'), unpack('<Q')
                if length > 10_000_000:
                    raise ValueError('Oversized GGUF array')
                if subtype in formats:
                    f.seek(length * struct.calcsize(formats[subtype]), 1)
                else:
                    for _ in range(length):
                        value(subtype, False, depth + 1)
                return None
            raise ValueError('Unsupported GGUF metadata type')

        if f.read(4) != b'GGUF' or unpack('<I') not in (2, 3):
            raise ValueError('Invalid GGUF header')
        tensor_count = unpack('<Q')
        count = unpack('<Q')
        if count > 1_000_000:
            raise ValueError('Oversized GGUF header')
        result = {}
        for _ in range(count):
            key = string()
            keep = key in ('general.architecture', 'general.name', 'general.type', 'general.alignment', 'split.count', 'split.no', 'tokenizer.chat_template') or key.endswith(('.context_length', '.nextn_predict_layers', '.block_count', '.expert_count', '.ple.ngram_size', '.ngram_size'))
            result_value = value(unpack('<I'), keep)
            if keep:
                result[key] = result_value
        if tensors and tensor_count < 1_000_000:
            entries = []
            for _ in range(tensor_count):
                name = string()
                dims = unpack('<I')
                if dims > 8:
                    raise ValueError('Invalid GGUF tensor shape')
                f.seek(8 * dims + 4, 1)
                entries.append((unpack('<Q'), name))
            alignment = int(result.get('general.alignment') or 32)
            start = -(-f.tell() // alignment) * alignment
            entries.sort()
            ends = [offset for offset, _ in entries[1:]] + [file_size - start]
            result['_tensor_bytes'] = max(0, file_size - start)
            result['_expert_bytes'] = sum(max(0, end - offset) for (offset, name), end in zip(entries, ends) if '_exps' in name)
            result['_ngram_bytes'] = sum(max(0, end - offset) for (offset, name), end in zip(entries, ends) if is_ngram_tensor(name))
    return result


def is_ngram_tensor(name):
    """The per-layer n-gram lookup table, which engines keep in system RAM or on storage, never on the GPU."""
    return 'ngram_embedding' in name or name.startswith('per_layer_token_embd')


def safetensors_weight_split(weights):
    """Bytes held by routed experts and by the n-gram table, read from safetensors headers only."""
    experts = ngram = 0
    for weight in weights:
        with weight.open('rb') as f:
            size_data = f.read(8)
            if len(size_data) != 8:
                continue
            size = struct.unpack('<Q', size_data)[0]
            if size > 64_000_000:
                continue
            header = json.loads(f.read(size))
        for k, v in header.items():
            if k == '__metadata__':
                continue
            size = v['data_offsets'][1] - v['data_offsets'][0]
            if '.experts.' in k:
                experts += size
            elif is_ngram_tensor(k):
                ngram += size
    return experts, ngram


def gguf_quant(name):
    """Quant labels start with a quant type and digit, never the Q in Qwen."""
    matches = re.findall(r'(?:^|[-_])((?:UD-)?(?:IQ\d|Q\d|MXFP\d|NVFP\d|BF16|F16|F32)[\w.]*)', name)
    return matches[-1] if matches else 'Mixed'


def gguf_projector(path, root):
    # Require an unambiguous checkpoint/family match. In particular, never take
    # the first projector in a directory containing several model families.
    def normalize(name):
        name = name.lower().replace('_', '-')
        name = re.sub(r'(?:^|-)mmproj(?:-|$)', '-', name)
        name = re.sub(r'-(?:ud-)?(?:iq\d|q\d|mxfp\d|nvfp\d|bf16|f16|f32).*$', '', name)
        return name.strip('-')
    model_name = normalize(path.stem)
    candidates = set(path.parent.glob('*mmproj*.gguf')) | set(root.glob('*mmproj*.gguf'))
    ranked = []
    family = re.match(r'qwen[\d.]+-\d+b(?:-a\d+b)?(?=-|$)', model_name)
    for candidate in candidates:
        candidate_name = normalize(candidate.stem)
        if candidate_name == model_name:
            ranked.append((2, candidate))
        elif family and candidate_name == family[0]:
            ranked.append((1, candidate))
    if ranked:
        best = max(score for score, _ in ranked)
        choices = [candidate for score, candidate in ranked if score == best]
        if len(choices) == 1:
            return str(choices[0])
    return None


def native_model(path, root):
    config = read_json(path / 'config.json')
    text = config.get('text_config', config)
    quant = config.get('quantization_config', text.get('quantization_config', {}))
    method = str(quant.get('quant_method', 'native'))
    architecture = config.get('architectures', [config.get('model_type', 'Unknown')])[0]
    weights = list(path.glob('*.safetensors'))
    issues = []
    names = set()
    index = path / 'model.safetensors.index.json'
    if index.exists():
        mapping = read_json(index).get('weight_map', {})
        names = set(mapping)
        for filename in set(mapping.values()):
            if not within(path, filename).is_file():
                issues.append('Missing shard: ' + filename)
    if not weights:
        issues.append('No model weight files')
    if not (path / 'tokenizer.json').is_file():
        issues.append('Missing tokenizer.json')
    if method == 'exl3':
        for weight in weights:
            with weight.open('rb') as f:
                size_data = f.read(8)
                if len(size_data) != 8:
                    issues.append('Incomplete file: ' + weight.name)
                    continue
                size = struct.unpack('<Q', size_data)[0]
                if size > 32_000_000:
                    issues.append('Invalid header: ' + weight.name)
                    continue
                header = json.loads(f.read(size))
                names.update(k for k in header if k != '__metadata__')
                end = max((v['data_offsets'][1] for k, v in header.items() if k != '__metadata__'), default=0)
                if 8 + size + end != weight.stat().st_size:
                    issues.append('Incomplete tensor data: ' + weight.name)
    vision = bool(config.get('vision_config') or config.get('vision_n_layers'))
    mtp = bool(text.get('mtp_num_hidden_layers') or text.get('num_nextn_predict_layers')) and any('mtp' in n or 'nextn' in n or 'dspark' in n for n in names | {p.name for p in weights})
    bits = quant.get('bits')
    quant_label = f'{bits:g} bpw' if isinstance(bits, (float, int)) else method.upper()
    if 'NVFP4' in path.name.upper():
        quant_label = 'NVFP4'
    receipt = None
    for receipt_path in (root / '.linux-llm-downloads').glob('*.json'):
        candidate = read_json(receipt_path)
        if candidate.get('folder') == path.name:
            receipt = {k: candidate.get(k) for k in ('status', 'commit', 'verified_utc', 'repo', 'revision')}
            for item in candidate.get('files', []):
                p = within(path, item['name'])
                if not p.is_file() or p.stat().st_size != item['size']:
                    issues.append('Receipt mismatch: ' + item['name'])
            break
    try:
        expert_bytes, ngram_bytes = safetensors_weight_split(weights)
    except (OSError, ValueError, KeyError, TypeError):
        expert_bytes = ngram_bytes = 0
    return dict(path=str(path), name=path.name, format='EXL3' if method == 'exl3' else 'Safetensors',
                quant=quant_label, architecture=architecture, reasoning=native_reasoning(path, method == 'exl3'),
                context=text.get('max_position_embeddings', 4096), vision=vision, mtp=mtp,
                draft_limit=min(16, max(1, int(text.get('dspark_block_size', 4)))),
                experts=text.get('num_experts', text.get('n_routed_experts', 0)),
                layers=text.get('num_hidden_layers', 0), ngram=bool(text.get('ngram_size')),
                bytes=sum(p.stat().st_size for p in weights), issues=list(dict.fromkeys(issues)),
                receipt=receipt, projector=None, expert_bytes=expert_bytes, ngram_bytes=ngram_bytes,
                recommended=method == 'exl3' and any(s in path.name for s in ['Flash-Next', 'Flash-0731', 'Vision-Exp']),
                engines=['exl3'] if method == 'exl3' else ['vllm'])


def read_receipts(root):
    receipts = {}
    for receipt_path in (root / '.linux-llm-downloads').glob('*.json'):
        try:
            candidate = read_json(receipt_path)
        except (OSError, ValueError):
            continue
        if isinstance(candidate, dict) and candidate.get('folder'):
            receipts[candidate['folder']] = {k: candidate.get(k) for k in ('status', 'commit', 'verified_utc', 'repo', 'revision')}
    return receipts


def folder_projector(path):
    """A downloaded model keeps its own folder: pair its single projector."""
    models = [p for p in path.parent.glob('*.gguf') if 'mmproj' not in p.name.lower()
              and not re.search(r'-(?!00001)\d{5}-of-\d{5}\.gguf$', p.name)]
    projectors = sorted(path.parent.glob('*mmproj*.gguf'))
    if len(models) == 1 and projectors:
        preferred = [p for p in projectors if re.search(r'(?i)(?:^|[-_.])f16', p.name)] or projectors
        return str(preferred[0])
    return None


def walk(base):
    # A bounded walk covers the library and nested quant folders, excluding Hub caches.
    dirs = [base]
    for _ in range(3):
        dirs += [p for parent in list(dirs) for p in parent.iterdir()
                 if p.is_dir() and not p.name.startswith('.') and p.name not in ('hub', 'xet')
                 and p not in dirs]
        dirs = list(dict.fromkeys(dirs))
    return dirs


def scan(root, extra=()):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Model folder is unavailable. Mount the model drive and refresh.')
    models = []
    errors = []
    receipts = read_receipts(root)
    dirs = walk(root)
    locations = {}
    for location in extra:
        location = Path(location).expanduser()
        if location.is_file() and location.suffix.lower() == '.gguf':
            dirs.append(location)
        elif location.is_dir():
            dirs += walk(location.resolve())
        else:
            errors.append(f'{location}: this added location is unavailable. Reconnect its drive or remove it from the library.')
        locations[str(location)] = True
    dirs = list(dict.fromkeys(dirs))
    for directory in dirs:
        if directory.is_dir() and (directory / 'config.json').is_file():
            try:
                models.append(native_model(directory, root))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f'{directory.name}: {exc}')
    for directory in dirs:
        for path in ([directory] if directory.is_file() else directory.glob('*.gguf')):
            lower = path.name.lower()
            if any(s in lower for s in ('mmproj', 'drafter', 'dspark', 'fastmtp')) or lower.startswith('mtp-'):
                continue
            match = re.search(r'-(\d{5})-of-(\d{5})\.gguf$', path.name)
            if match and int(match[1]) != 1:
                continue
            try:
                meta = gguf_metadata(path, tensors=True)
                if meta.get('general.type') in ('mmproj', 'adapter'):
                    continue
                shards = [path] if not match else [path.with_name(path.name[:match.start()] + f'-{i:05d}-of-{int(match[2]):05d}.gguf') for i in range(1, int(match[2]) + 1)]
                issues = ['Missing shard: ' + p.name for p in shards if not p.is_file()]
                expert_bytes, ngram_bytes = meta.get('_expert_bytes', 0), meta.get('_ngram_bytes', 0)
                for shard in shards[1:]:
                    if shard.is_file():
                        try:
                            shard_meta = gguf_metadata(shard, tensors=True)
                            expert_bytes += shard_meta.get('_expert_bytes', 0)
                            ngram_bytes += shard_meta.get('_ngram_bytes', 0)
                        except (OSError, ValueError, struct.error):
                            pass
                arch = meta.get('general.architecture', 'unknown')
                projector = gguf_projector(path, root) or folder_projector(path)
                # Retain explicitly known legacy projector pairs too.
                if not projector:
                    for tag, filename in [('DeepSeek-V4-Flash-Vision-Exp', 'mmproj-DeepSeek-V4-Flash-Vision-Exp-Q8_0.gguf'), ('GLM-5.3-Flash', 'mmproj-zai-org.GLM-5.3-Flash.f16.gguf')]:
                        if tag in path.name and (root / filename).is_file():
                            projector = str(root / filename)
                nextn = int(meta.get(arch + '.nextn_predict_layers', 0))
                mtp = nextn > 0 and arch in ('qwen35', 'qwen35moe')
                template = meta.get('tokenizer.chat_template', '')
                receipt = receipts.get(path.parent.name) if path.parent != root else None
                models.append(dict(path=str(path), name=path.name[:match.start()] if match else path.stem,
                    format='GGUF', quant=gguf_quant(path.name[:match.start()] if match else path.stem),
                    architecture=arch, context=meta.get(arch + '.context_length', 4096), reasoning=inspect_reasoning(template),
                    vision=bool(projector), mtp=mtp, mtp_kind='embedded' if mtp else None,
                    mtp_note='Embedded MTP weights' if mtp else ('MTP is not supported for this architecture yet' if nextn else 'This GGUF does not contain MTP weights'),
                    tool_format='llama-jinja' if arch in ('qwen35', 'qwen35moe') and '<tool_call>' in template and 'tools' in template else None,
                    experts=meta.get(arch + '.expert_count', 0), layers=meta.get(arch + '.block_count', 0),
                    draft_limit=4, nextn_layers=nextn,
                    ngram=bool(meta.get(arch + '.ple.ngram_size') or meta.get(arch + '.ngram_size')),
                    bytes=sum(p.stat().st_size for p in shards if p.is_file()), issues=issues,
                    receipt=receipt, projector=projector, recommended=False, engines=['gguf'],
                    expert_bytes=expert_bytes, ngram_bytes=ngram_bytes))
            except (OSError, ValueError, KeyError, struct.error) as exc:
                errors.append(f'{path.name}: {exc}')
    for model in models:
        path = Path(model['path'])
        inside = path.is_relative_to(root)
        model['id'] = hashlib.sha256(str(path.relative_to(root) if inside else path).encode()).hexdigest()[:16]
        model['title'] = re.sub(r'[-_]?(EXL3.*|NVFP4.*|UD-.*)$', '', model['name']).replace('-', ' ')
        model['location'] = 'library' if inside else 'added'
        family, variant = split_name(model['name'] if model['format'] == 'GGUF' or path.name == model['name'] else path.name)
        model['family'], model['variant'] = family, variant
        active = active_billions(model['name'])
        model['active_b'] = active
        if model.get('receipt') and model['receipt'].get('repo'):
            model['source'] = model['receipt']['repo']
    models.sort(key=lambda m: (not m['recommended'], not m['name'].startswith('Qwen3.8-Flash-Next-EXL3'), m['name']))
    return {'models': models, 'errors': errors, 'root': str(root)}

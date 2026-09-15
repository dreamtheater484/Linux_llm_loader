"""Inspect model metadata without importing model code or loading tensor data."""
import hashlib
import json
from pathlib import Path
import re
import struct


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('A model file points outside its model directory.')
    return path


def gguf_metadata(path):
    """Read selected GGUF fields, skipping token arrays and tensor payloads."""
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
        unpack('<Q')
        count = unpack('<Q')
        if count > 1_000_000:
            raise ValueError('Oversized GGUF header')
        result = {}
        for _ in range(count):
            key = string()
            keep = key in ('general.architecture', 'general.name', 'general.type', 'split.count', 'split.no') or key.endswith(('.context_length', '.nextn_predict_layers', '.block_count'))
            result_value = value(unpack('<I'), keep)
            if keep:
                result[key] = result_value
        return result


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
            receipt = {k: candidate.get(k) for k in ('status', 'commit', 'verified_utc')}
            for item in candidate.get('files', []):
                p = within(path, item['name'])
                if not p.is_file() or p.stat().st_size != item['size']:
                    issues.append('Receipt mismatch: ' + item['name'])
            break
    return dict(path=str(path), name=path.name, format='EXL3' if method == 'exl3' else 'Safetensors',
                quant=quant_label, architecture=architecture,
                context=text.get('max_position_embeddings', 4096), vision=vision, mtp=mtp,
                draft_limit=min(16, max(1, int(text.get('dspark_block_size', 4)))),
                experts=text.get('num_experts', text.get('n_routed_experts', 0)),
                layers=text.get('num_hidden_layers', 0), ngram=bool(text.get('ngram_size')),
                bytes=sum(p.stat().st_size for p in weights), issues=list(dict.fromkeys(issues)),
                receipt=receipt, projector=None,
                recommended=method == 'exl3' and any(s in path.name for s in ['Flash-Next', 'Flash-0731', 'Vision-Exp']),
                engines=['exl3'] if method == 'exl3' else ['vllm'])


def scan(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Model folder is unavailable. Mount the model drive and refresh.')
    models = []
    errors = []
    # A bounded walk covers the library and nested quant folders, excluding Hub caches.
    dirs = [root]
    for _ in range(3):
        dirs += [p for parent in list(dirs) for p in parent.iterdir()
                 if p.is_dir() and not p.name.startswith('.') and p.name not in ('hub', 'xet')
                 and p not in dirs]
        dirs = list(dict.fromkeys(dirs))
    for directory in dirs:
        if (directory / 'config.json').is_file():
            try:
                models.append(native_model(directory, root))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f'{directory.name}: {exc}')
    for directory in dirs:
        for path in directory.glob('*.gguf'):
            lower = path.name.lower()
            if any(s in lower for s in ('mmproj', 'drafter', 'dspark', 'mtp-')):
                continue
            match = re.search(r'-(\d{5})-of-(\d{5})\.gguf$', path.name)
            if match and int(match[1]) != 1:
                continue
            try:
                meta = gguf_metadata(path)
                if meta.get('general.type') in ('mmproj', 'adapter'):
                    continue
                shards = [path] if not match else [path.with_name(path.name[:match.start()] + f'-{i:05d}-of-{int(match[2]):05d}.gguf') for i in range(1, int(match[2]) + 1)]
                issues = ['Missing shard: ' + p.name for p in shards if not p.is_file()]
                arch = meta.get('general.architecture', 'unknown')
                projector = None
                for tag, filename in [('Qwen3.8-Flash-Next', 'Qwen3.8-Flash-Next-mmproj-F16.gguf'), ('DeepSeek-V4-Flash-Vision-Exp', 'mmproj-DeepSeek-V4-Flash-Vision-Exp-Q8_0.gguf'), ('GLM-5.3-Flash', 'mmproj-zai-org.GLM-5.3-Flash.f16.gguf')]:
                    if tag in path.name and (root / filename).is_file():
                        projector = str(root / filename)
                models.append(dict(path=str(path), name=path.name[:match.start()] if match else path.stem,
                    format='GGUF', quant=next(iter(re.findall(r'(?:UD-)?(?:IQ|Q|MXFP|NVFP)[\w.]+', path.stem)), 'Mixed'),
                    architecture=arch, context=meta.get(arch + '.context_length', 4096),
                    vision=bool(projector), mtp=False, experts=0, layers=meta.get(arch + '.block_count', 0),
                    draft_limit=4,
                    ngram=False, bytes=sum(p.stat().st_size for p in shards if p.is_file()), issues=issues,
                    receipt=None, projector=projector, recommended=False, engines=['gguf']))
            except (OSError, ValueError, KeyError, struct.error) as exc:
                errors.append(f'{path.name}: {exc}')
    for model in models:
        model['id'] = hashlib.sha256(str(Path(model['path']).relative_to(root)).encode()).hexdigest()[:16]
        model['title'] = re.sub(r'[-_]?(EXL3.*|NVFP4.*|UD-.*)$', '', model['name']).replace('-', ' ')
    models.sort(key=lambda m: (not m['recommended'], not m['name'].startswith('Qwen3.8-Flash-Next-EXL3'), m['name']))
    return {'models': models, 'errors': errors, 'root': str(root)}

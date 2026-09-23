"""Group model files and repositories into families people recognise.

A family is the model as its authors named it (``Qwen3.6-27B``); everything
else in a name is either packaging (GGUF, EXL3, a quant label) or a variant
(``Uncensored Heretic``, ``Instruct 2512``)."""
import re

QUANT = (r'(?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|MXFP\d\w*|NVFP4\w*|BF16|F16|F32|FP8|FP16|INT[48]'
         r'|\d+(?:\.\d+)?bpw\w*|[hH]\d+|ng\d+)')
_PACKAGING = re.compile(r'(?i)[-_.](?:gguf|exl3|exl2|mlx)(?:[-_.].*)?$')
_TRAILING_QUANT = re.compile(r'(?i)[-_.]' + QUANT + r'$')
_QUANT_TOKEN = re.compile(r'(?i)^' + QUANT + r'$')
_SIZE = re.compile(r'^(.*?(?<![A-Za-z0-9.])E?\d+(?:\.\d+)?[BbMm](?:-A\d+(?:\.\d+)?[Bb])?)(?=$|[-_ .])')
_NOISE = {'gguf', 'exl3', 'imatrix', 'i1', 'llama.cpp', 'llamacpp', 'ud', 'quants', 'quantized', 'model', 'weights'}


def clean_name(name):
    name = re.sub(r'(?i)\.gguf$', '', name)
    name = re.sub(r'-\d{5}-of-\d{5}$', '', name)
    # Downloader prefixes such as "Q4_K_M__" or "UD-Q4_K_XL__".
    name = re.sub(r'^(?:UD-)?[A-Z0-9_]+__', '', name)
    # Organisation prefixes: "zai-org.GLM-5.3" and bartowski's "Qwen_Qwen3.5-9B".
    name = re.sub(r'^[a-z][\w-]*\.(?=[A-Z])', '', name)
    name = re.sub(r'^([A-Za-z][A-Za-z0-9]*)_(?=[A-Za-z])', '', name)
    name = re.sub(r'(?i)\.(?:f16|bf16|f32)(?:\..*)?$', '', name)
    return name


def split_name(name):
    """Return (family, variant) for a file, folder or repository name."""
    name = clean_name(name)
    name = _PACKAGING.sub('', name)
    while True:
        stripped = _TRAILING_QUANT.sub('', name)
        if stripped == name or not stripped:
            break
        name = stripped
    match = _SIZE.match(name)
    family, rest = (match.group(1), name[match.end():]) if match else (name, '')
    words = [w.replace('_', ' ') for w in re.split(r'[-\s]+', rest) if w and not _QUANT_TOKEN.match(w) and w.lower() not in _NOISE]
    return family.strip('-_ .'), ' '.join(words)


def family_key(family):
    key = re.sub(r'[\s_]+', '-', family.strip().lower())
    # "Qwen 3.5" and "gemma-4" group with "Qwen3.5" and "gemma4".
    return re.sub(r'(?<![a-z0-9.])([a-z]+)-(\d+(?:\.\d+)*)(?=-|$)', r'\1\2', key)


def merge_families(keys):
    """Map each family key to the shortest related key present in the set.

    "huihui-qwen3.6-35b-a3b" joins "qwen3.6-35b-a3b", and
    "qwen3.8-flash-next-uncensored" joins "qwen3.8-flash-next". Returns
    {key: (target, extra_variant_words)}."""
    keys = set(keys)
    result = {}
    for key in keys:
        best, extra = key, ''
        for other in keys:
            if other == key or len(other) >= len(best) or len(other) < 6:
                continue
            if key.endswith('-' + other):
                best, extra = other, key[:-len(other) - 1]
            elif key.startswith(other + '-') and not re.search(r'\d+(?:\.\d+)?b', key[len(other):]):
                best, extra = other, key[len(other) + 1:]
        result[key] = (best, extra.replace('-', ' '))
    return result


def display(family):
    return family.replace('_', ' ').replace('-', ' ').strip()


def active_billions(name):
    match = re.search(r'(?i)(?<![A-Za-z0-9])A(\d+(?:\.\d+)?)B(?![A-Za-z])', name)
    return float(match.group(1)) if match else None


def quant_label(name):
    matches = re.findall(r'(?i)(?:^|[-_./])(' + QUANT + r')(?=$|[-_./])', name)
    labels = [m for m in matches if not re.fullmatch(r'(?i)[hH]\d+|ng\d+', m)]
    return labels[-1] if labels else None

#!/usr/bin/env python3
"""Prepare a supported ExLlama/Tabby runtime beside the existing installation.

Never mutate the active Python environment. The caller activates the receipt's
paths only after this command succeeds. This does not update system packages.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def install(runtime, receipt):
    lock = json.loads((PROJECT / 'runtime-locks/exllama.json').read_text())
    uv = runtime / 'bin/uv'
    if not uv.is_file():
        uv = shutil.which('uv')
    if not uv and sys.version_info[:2] != (3, 12):
        raise RuntimeError('This update needs Python 3.12 or Inflect’s private uv package manager. Run setup first.')
    if shutil.disk_usage(runtime).free < 12 * 2**30:
        raise RuntimeError('At least 12 GiB free space is needed to prepare an ExLlama update.')
    parent = runtime / 'exl3-releases'
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"{lock['version']}-", dir=parent))
    env = {**os.environ, 'UV_CACHE_DIR': str(runtime / 'cache/uv'),
           'UV_PYTHON_INSTALL_DIR': str(runtime / 'python'), 'GIT_TERMINAL_PROMPT': '0',
           'PIP_CACHE_DIR': str(runtime / 'cache/pip'), 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    def run(*args):
        subprocess.run([str(arg) for arg in args], cwd=PROJECT, env=env, check=True)
    try:
        python = stage / 'env/bin/python'
        tabby = stage / 'tabbyAPI'
        if uv:
            run(uv, 'venv', '--python', lock['python'], '--seed', stage / 'env')
            installer = [uv, 'pip', 'install', '--python', python]
        else:
            # Earlier installations already supply Python 3.12 but predate uv.
            run(sys.executable, '-m', 'venv', stage / 'env')
            installer = [python, '-m', 'pip', 'install']
        run('git', 'clone', '--no-checkout', 'https://github.com/theroyallab/tabbyAPI.git', tabby)
        run('git', '-C', tabby, 'checkout', '--detach', lock['tabby_revision'])
        for patch in ('tabby-exl3-model-card.patch', 'tabby-qwen-tool-schema.patch'):
            run('git', '-C', tabby, 'apply', PROJECT / 'patches' / patch)
        run(sys.executable, PROJECT / 'scripts/fetch_exl_wheel.py')
        run(*installer, '--extra-index-url', 'https://pypi.nvidia.com',
            '-r', PROJECT / 'runtime-locks/exl3-dependencies.txt',
            'torch @ https://download.pytorch.org/whl/cu128/torch-2.9.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=87c62d3b95f1a2270bd116dbd47dc515c0b2035076fbb4a03b4365ea289e89c4',
            PROJECT / '.runtime/wheels/exllamav3-1.5.0+cu128.torch2.9.0-cp312-cp312-linux_x86_64.whl', tabby)
        site = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
        run('git', '-C', site, 'apply', PROJECT / 'patches/exl3-cumulative-output-tokens.patch')
        run(python, '-c', 'import torch, exllamav3, importlib.metadata; '
            f'assert importlib.metadata.version("exllamav3") == {lock["package_version"]!r}; '
            f'assert torch.__version__ == {lock["torch"]!r}; '
            'assert torch.cuda.is_available(), "CUDA is unavailable"; print("ExLlama and CUDA verified")')
        run(python, '-m', 'pip', 'check')
        # The environment keeps its original path: virtualenv scripts contain it.
        receipt.write_text(json.dumps(dict(exl_python=str(python), tabby_dir=str(tabby), **lock)))
        print('Supported ExLlama runtime prepared. The previous installation is intact.', flush=True)
    except BaseException:
        shutil.rmtree(stage)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    install(args.runtime_dir.expanduser().resolve(), args.receipt)

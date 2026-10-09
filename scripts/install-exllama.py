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
sys.path.insert(0, str(PROJECT))
from inflect_platform import exllama_lock, program, venv_python


def install(runtime, receipt):
    lock = exllama_lock(PROJECT)
    uv = program(runtime / 'bin/uv')
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
        python = venv_python(stage / 'env')
        tabby = stage / 'tabbyAPI'
        if uv:
            run(uv, 'venv', '--python', lock['python'], '--seed', stage / 'env')
            installer = [uv, 'pip', 'install', '--python', python]
        else:
            # Earlier installations already supply Python 3.12 but predate uv.
            run(sys.executable, '-m', 'venv', stage / 'env')
            installer = [python, '-m', 'pip', 'install']
        # Upstream line endings, whatever this machine's core.autocrlf: the patches expect LF.
        run('git', '-c', 'core.autocrlf=false', 'clone', '--no-checkout', 'https://github.com/theroyallab/tabbyAPI.git', tabby)
        run('git', '-C', tabby, 'config', 'core.autocrlf', 'false')
        run('git', '-C', tabby, 'checkout', '--detach', lock['tabby_revision'])
        for patch in ('tabby-exl3-model-card.patch', 'tabby-qwen-tool-schema.patch'):
            run('git', '-C', tabby, 'apply', PROJECT / 'patches' / patch)
        run(sys.executable, PROJECT / 'scripts/fetch_exl_wheel.py')
        index = ['--extra-index-url', lock['extra_index_url']] if lock.get('extra_index_url') else []
        run(*installer, *index, '-r', PROJECT / lock['dependencies'],
            'torch @ ' + lock['torch_wheel'], PROJECT / '.runtime/wheels' / lock['wheel'], tabby)
        site = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
        for patch in lock.get('site_patches', []):
            run('git', '-C', site, 'apply', PROJECT / patch)
        run(python, '-c', 'import torch, exllamav3, importlib.metadata; '
            f'assert importlib.metadata.version("exllamav3") == {lock["package_version"]!r}; '
            f'assert torch.__version__ == {lock["torch"]!r}; '
            'assert torch.cuda.is_available(), "CUDA is unavailable"; print("ExLlama and CUDA verified")')
        run(python, '-m', 'pip', 'check')
        # The environment keeps its original path: virtualenv scripts contain it.
        receipt.write_text(json.dumps(dict(exl_python=str(python), tabby_dir=str(tabby),
                                           **{k: lock[k] for k in ('version', 'package_version', 'tabby_revision', 'torch', 'python')})),
                           encoding='utf-8')
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

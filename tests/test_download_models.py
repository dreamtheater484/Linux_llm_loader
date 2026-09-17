"""Offline control-flow checks; no model downloads or Windows runtime required."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'download-models.ps1'
SOURCE = SCRIPT.read_text(encoding='utf-8').split("$downloadCode = @'\n", 1)[1].split("\n'@", 1)[0]
helper = types.ModuleType('download_models')
exec(compile(SOURCE, str(SCRIPT) + ':embedded-python', 'exec'), helper.__dict__)
SHA = 'a' * 40
FILES = {'config.json': b'{}', 'tokenizer_config.json': b'{}', 'model.safetensors': b'test'}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='model downloads ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = helper.CATALOG[0]
        self.api = Mock()
        self.api.model_info.return_value = types.SimpleNamespace(
            sha=SHA, siblings=[types.SimpleNamespace(rfilename=n, size=len(b)) for n, b in FILES.items()])
        self.output = contextlib.ExitStack()
        self.output.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.output.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(self.output.close)

    def plan(self):
        return helper.make_plan(self.api, self.root, [self.spec])[0]

    def populate(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in FILES.items():
            (folder / name).write_bytes(data)

    def receipt(self, spec=None):
        return self.root / '.linux-llm-downloads' / ((spec or self.spec)['id'] + '.json')

    def run_main(self, extra=(), free=100 * 2**30, downloader=None):
        hub = types.ModuleType('huggingface_hub')
        hub.HfApi = Mock(return_value=self.api)
        hub.snapshot_download = downloader or Mock()
        with patch.dict(sys.modules, huggingface_hub=hub), patch.dict(os.environ), \
                patch.object(helper, 'download_http', downloader or Mock()), \
                patch.object(sys, 'argv', ['download_models', '--root', str(self.root), '--hf', 'fake hf.exe', *extra]), \
                patch.object(helper.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=free)):
            return helper.main()

    def test_default_stays_three_bit_and_qwen4_is_explicit(self):
        self.assertEqual([m['revision'] for m in helper.select_models(helper.DEFAULT_MODELS)],
                         ['3.05bpw_h5_ng5', '3.04bpw', '3.04bpw'])
        self.assertEqual(len(helper.select_models('All')), 4)
        qwen4 = helper.select_models('QWEN4, qwen4')
        self.assertEqual(len(qwen4), 1)
        self.assertEqual(qwen4[0]['revision'], '4.05bpw_h6_ng6')
        self.assertNotEqual(qwen4[0]['folder'], self.spec['folder'])
        self.assertEqual(helper.select_models('QWEN3, qwen3'), [self.spec])
        for invalid in ('', 'Qwen5', 'All,invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                helper.select_models(invalid)

    def test_qwen4_requires_vision_and_embeddings_and_has_separate_receipt(self):
        spec = helper.select_models('Qwen4')[0]
        old = self.plan()
        helper.write_json(self.receipt(), old)
        with self.assertRaisesRegex(ValueError, 'vision/embedding'):
            helper.make_plan(self.api, self.root, [spec])
        self.api.model_info.return_value.siblings.extend([
            types.SimpleNamespace(rfilename=name, size=5) for name in spec['required_files']])
        plan = helper.make_plan(self.api, self.root, [spec])[0]
        helper.write_json(self.receipt(spec), plan)
        self.assertEqual(json.loads(self.receipt().read_text()), old)
        self.assertEqual(plan['commit'], SHA)
        self.api.reset_mock()
        helper.make_plan(self.api, self.root, [spec])
        self.api.model_info.assert_called_once_with(spec['repo'], revision=SHA, files_metadata=True, timeout=30)

    def test_paths_with_spaces_work_and_escape_is_rejected(self):
        self.assertEqual(helper.local_file(self.root, 'nested/file.json'), self.root / 'nested/file.json')
        for name in ('../outside', '/absolute', 'C:/outside', 'nested/../../outside', 'nested\\outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                helper.local_file(self.root, name)

    def test_resume_reuses_saved_commit_and_rejects_changed_identity(self):
        plan = self.plan()
        helper.write_json(self.receipt(), plan)
        self.api.reset_mock()
        self.plan()
        self.api.model_info.assert_called_once_with(self.spec['repo'], revision=SHA, files_metadata=True, timeout=30)
        plan['repo'] = 'different/repo'
        helper.write_json(self.receipt(), plan)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            self.plan()

    def test_space_budget_counts_missing_and_incomplete_files(self):
        folder = self.root / self.spec['folder']
        self.populate(folder)
        self.assertEqual(self.plan()['missing_bytes'], 0)
        (folder / 'model.safetensors').write_bytes(b'x')
        (folder / 'config.json').unlink()
        self.assertEqual(self.plan()['missing_bytes'], 6)

    def test_metadata_failure_prevents_all_weight_downloads(self):
        downloader = Mock()
        info = self.api.model_info.return_value
        self.api.model_info.side_effect = [info, RuntimeError('repository unavailable')]
        with self.assertRaisesRegex(RuntimeError, 'repository unavailable'):
            self.run_main(downloader=downloader)
        downloader.assert_not_called()
        self.assertFalse(self.receipt().exists())

    def test_missing_index_shard_is_rejected(self):
        plan = self.plan()
        folder = self.root / self.spec['folder']
        self.populate(folder)
        (folder / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'layer': 'absent.safetensors'}}))
        with self.assertRaisesRegex(ValueError, 'missing shard'):
            helper.check_files(plan, folder)

    def test_download_uses_pinned_plan_then_verifies(self):
        plan = self.plan()
        folder = self.root / self.spec['folder']
        self.populate(folder)
        downloader = Mock()
        with patch.object(helper.time, 'sleep') as sleep, patch.object(helper.subprocess, 'run') as verify:
            helper.download_one(plan, self.root, 'C:/tools with spaces/hf.exe', downloader, 2, False)
        downloader.assert_called_once_with(plan, self.root, 2)
        self.assertEqual(downloader.call_args.args[0]['commit'], SHA)
        sleep.assert_not_called()
        self.assertIn('--fail-on-missing-files', verify.call_args.args[0])
        self.assertEqual(verify.call_args.kwargs, {'check': True})
        self.assertEqual(json.loads(self.receipt().read_text())['status'], 'verified')

    def test_checksum_failure_cannot_record_success(self):
        plan = self.plan()
        self.populate(self.root / self.spec['folder'])
        downloader = Mock()
        with patch.object(helper.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'hf')):
            result = self.run_main(['--models', 'Qwen3', '--verify-only'], downloader=downloader)
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(self.receipt().read_text())['status'], 'failed')
        downloader.assert_not_called()

    def test_preview_and_insufficient_space_do_not_download_or_pin(self):
        downloader = Mock()
        self.assertEqual(self.run_main(['--list'], free=0, downloader=downloader), 0)
        with self.assertRaisesRegex(RuntimeError, 'Not enough free space'):
            self.run_main(free=0, downloader=downloader)
        downloader.assert_not_called()
        self.assertFalse(self.receipt().exists())

    def test_all_models_pinned_before_download_and_failure_does_not_stop_queue(self):
        visits = []

        def fake_download(plan, root, hf, downloader, workers, verify_only):
            for spec in helper.select_models(helper.DEFAULT_MODELS):
                self.assertEqual(json.loads(self.receipt(spec).read_text())['commit'], SHA)
            visits.append(plan['id'])
            if plan['id'] == 'Qwen3':
                raise RuntimeError('first transfer failed')

        with patch.object(helper, 'download_one', side_effect=fake_download):
            self.assertEqual(self.run_main(), 1)
        self.assertEqual(visits, ['Qwen3', 'DeepSeek3', 'DeepSeekVision3'])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell required')
    def test_powershell_blocks_existing_downloader_before_startup(self):
        script_path = str(SCRIPT).replace("'", "''")
        command = """
function Get-CimInstance { [pscustomobject]@{ CommandLine = 'python download_models.py --root D:\\Documents\\AI\\models' } }
try {
    & 'SCRIPT_PATH' -ModelRoot 'D:\\Documents\\AI\\models' -UseHttp
    throw 'Guard did not block the second downloader.'
} catch {
    if ($_.Exception.Message -notlike 'A downloader is already using*') { throw }
    Write-Output 'GUARD PASSED'
}
""".replace('SCRIPT_PATH', script_path)
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('GUARD PASSED', result.stdout)
        self.assertNotIn('Checking downloader dependencies', result.stdout)


if __name__ == '__main__':
    unittest.main()

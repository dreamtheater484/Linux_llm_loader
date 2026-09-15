"""Exercise real HTTP sockets and on-disk resume state; no model downloads."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx

import test_download_models as control

helper = control.helper


class HttpTransferTests(unittest.TestCase):
    plan = control.DownloadTests.plan
    run_main = control.DownloadTests.run_main

    def setUp(self):
        control.DownloadTests.setUp(self)
        self.data = bytes(range(256)) * 4096
        self.entry = dict(name='nested/model.safetensors', size=len(self.data))
        self.download_plan = dict(self.spec, commit='a' * 40, files=[self.entry])
        self.requests = []
        self.mode = 'normal'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get('Range')))
                start, end = map(int, self.headers['Range'].removeprefix('bytes=').split('-'))
                first = len(owner.requests) == 1
                if owner.mode in ('expired', 'rate_limit', 'server_error') and first:
                    self.send_response({'expired': 403, 'rate_limit': 429, 'server_error': 503}[owner.mode])
                    self.send_header('Retry-After', '0')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if owner.mode in ('unauthorized', 'missing'):
                    self.send_response(401 if owner.mode == 'unauthorized' else 404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if owner.mode == 'ignore_range':
                    payload = owner.data
                    self.send_response(200)
                else:
                    payload = owner.data[start:end + 1]
                    self.send_response(206)
                    advertised_start = start + 1 if owner.mode == 'wrong_range' else start
                    self.send_header('Content-Range', f'bytes {advertised_start}-{end}/{len(owner.data)}')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    if owner.mode in ('drop_once', 'stall_once') and first:
                        self.wfile.write(payload[:len(payload) // 2])
                        self.wfile.flush()
                        if owner.mode == 'stall_once':
                            time.sleep(0.3)
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        self.close_connection = True
                    else:
                        self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.close_server)
        self.output.enter_context(patch.object(helper, 'HUB_URL', f'http://127.0.0.1:{self.server.server_port}'))
        self.output.enter_context(patch.object(helper, 'RANGE_BYTES', 512 * 1024))
        self.output.enter_context(patch.object(helper, 'READ_BYTES', 64 * 1024))
        self.output.enter_context(patch.object(helper, 'MAX_FAILURES', 3))
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.return_value = False

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def part(self):
        return helper.partial_path(self.download_plan, self.root, self.entry)

    def destination(self):
        return self.root / self.spec['folder'] / self.entry['name']

    def transfer(self, timeout=2):
        with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            helper.transfer_file(self.download_plan, self.root, self.entry, client, {}, self.stop)

    def assert_complete(self):
        self.assertEqual(self.destination().read_bytes(), self.data)
        self.assertFalse(self.part().exists())

    def test_real_http_ranges_produce_exact_file(self):
        self.transfer()
        self.assert_complete()
        self.assertEqual([r[1] for r in self.requests], ['bytes=0-524287', 'bytes=524288-1048575'])

    def test_dropped_connection_resumes_saved_bytes(self):
        self.mode = 'drop_once'
        self.transfer()
        self.assert_complete()
        self.assertEqual(self.requests[1][1], 'bytes=262144-786431')
        self.stop.wait.assert_called_once_with(2)

    def test_inactive_socket_times_out_then_resumes(self):
        self.mode = 'stall_once'
        self.transfer(timeout=0.1)
        self.assert_complete()
        self.assertEqual(self.requests[1][1], 'bytes=262144-786431')

    def test_expired_link_gets_fresh_hub_url(self):
        self.mode = 'expired'
        self.transfer()
        self.assert_complete()
        self.assertNotEqual(self.requests[0][0], self.requests[1][0])
        self.assertIn('/resolve/' + self.download_plan['commit'], self.requests[1][0])

    def test_rate_limit_and_server_error_are_retried(self):
        for mode in ('rate_limit', 'server_error'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.requests.clear()
                if self.destination().exists():
                    self.destination().unlink()
                self.transfer()
                self.assert_complete()

    def test_new_invocation_resumes_prior_partial(self):
        self.part().parent.mkdir(parents=True)
        self.part().write_bytes(self.data[:12345])
        self.transfer()
        self.assert_complete()
        self.assertEqual(self.requests[0][1], 'bytes=12345-536632')

    def test_persistent_auth_failure_is_bounded_and_preserves_partial(self):
        self.mode = 'unauthorized'
        self.part().parent.mkdir(parents=True)
        self.part().write_bytes(self.data[:12345])
        with self.assertRaisesRegex(RuntimeError, '3 consecutive transfer failures'):
            self.transfer()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.part().read_bytes(), self.data[:12345])
        self.assertFalse(self.destination().exists())

    def test_ignored_resume_does_not_append_or_destroy_partial(self):
        self.mode = 'ignore_range'
        self.part().parent.mkdir(parents=True)
        self.part().write_bytes(self.data[:12345])
        with self.assertRaisesRegex(ValueError, 'ignored resume Range'):
            self.transfer()
        self.assertEqual(self.part().read_bytes(), self.data[:12345])
        self.assertEqual(len(self.requests), 1)

    def test_200_without_range_is_supported_only_from_zero(self):
        self.mode = 'ignore_range'
        self.transfer()
        self.assert_complete()

    def test_incorrect_range_is_rejected_before_writing(self):
        self.mode = 'wrong_range'
        with self.assertRaisesRegex(ValueError, 'Invalid Content-Range'):
            self.transfer()
        self.assertEqual(self.part().stat().st_size, 0)

    def test_missing_file_fails_without_repeated_requests(self):
        self.mode = 'missing'
        with self.assertRaisesRegex(ValueError, 'HTTP 404'):
            self.transfer()
        self.assertEqual(len(self.requests), 1)

    def test_cancellation_leaves_bytes_for_next_invocation(self):
        self.stop.is_set.side_effect = [False, False, True]
        with self.assertRaises(InterruptedError):
            self.transfer()
        self.assertEqual(self.part().read_bytes(), self.data[:64 * 1024])
        self.assertFalse(self.destination().exists())
        self.stop.is_set.side_effect = None
        self.stop.is_set.return_value = False
        self.transfer()
        self.assert_complete()

    def test_completed_file_is_reused_without_network(self):
        self.destination().parent.mkdir(parents=True)
        self.destination().write_bytes(self.data)
        self.transfer()
        self.assertEqual(self.requests, [])

    def test_completed_partial_is_promoted_without_network(self):
        self.part().parent.mkdir(parents=True)
        self.part().write_bytes(self.data)
        self.transfer()
        self.assert_complete()
        self.assertEqual(self.requests, [])

    def test_http_partial_is_deducted_from_space_budget(self):
        entry = dict(name='model.safetensors', size=4)
        part = helper.partial_path(self.download_plan, self.root, entry)
        part.parent.mkdir(parents=True)
        part.write_bytes(b'te')
        self.assertEqual(self.plan()['missing_bytes'], 6)

    def test_root_lock_blocks_second_downloader(self):
        from filelock import FileLock
        path = self.root / '.linux-llm-downloads' / 'download.lock'
        path.parent.mkdir(parents=True)
        with FileLock(path), self.assertRaisesRegex(RuntimeError, 'Another downloader'):
            self.run_main()

    def test_logs_remove_signed_urls_and_tokens(self):
        text = helper.safe_error(Exception('failed https://cdn.test/file?token=SECRET hf_ABC123 Bearer XYZ'))
        for secret in ('SECRET', 'hf_ABC123', 'XYZ'):
            self.assertNotIn(secret, text)

    def test_cross_origin_redirect_does_not_forward_hub_token(self):
        seen = []

        def handle(request):
            seen.append(request)
            if request.url.host == 'huggingface.co':
                return httpx.Response(302, headers={'Location': 'https://cdn.test/file?signature=test'})
            return httpx.Response(200, content=b'x')

        with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            client.get('https://huggingface.co/file', headers={'Authorization': 'Bearer private'})
        self.assertEqual(seen[0].headers['Authorization'], 'Bearer private')
        self.assertNotIn('Authorization', seen[1].headers)

    def test_two_workers_download_distinct_files(self):
        second = dict(name='nested/second.safetensors', size=len(self.data))
        plan = dict(self.download_plan, files=[self.entry, second])
        with patch('huggingface_hub.utils.build_hf_headers', return_value={}):
            helper.download_http(plan, self.root, 2)
        self.assert_complete()
        self.assertEqual((self.root / self.spec['folder'] / second['name']).read_bytes(), self.data)

    def test_retry_after_and_exponential_backoff(self):
        self.mode = 'unauthorized'
        with patch.object(helper, 'retry_delay', return_value=90):
            with self.assertRaises(RuntimeError):
                self.transfer()
        self.assertEqual([c.args[0] for c in self.stop.wait.call_args_list], [90, 90])


if __name__ == '__main__':
    unittest.main()

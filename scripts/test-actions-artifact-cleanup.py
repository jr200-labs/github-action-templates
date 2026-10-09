#!/usr/bin/env python3
"""Exercise cleanup with inventory races, active runs and interrupted deletes."""
import importlib.util
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cleanup', Path(__file__).resolve().parents[1] / 'consumers/files/.shared/cleanup-actions-artifacts.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

class API:
    def __init__(self):
        self.items = {i: {'id': i, 'name': 'report', 'size_in_bytes': 60, 'created_at': date, 'workflow_run': {'id': i}} for i, date in [(1, '2026-09-27T00:00:00Z'), (2, '2026-10-08T00:00:00Z'), (3, '2026-09-27T00:00:00Z')]}
        self.deleted = []
        self.fail = False
        self.mismatch = False
    def artifacts(self):
        return deepcopy(list(self.items.values()))
    def request(self, path, method='GET'):
        number = int(path.rsplit('/', 1)[1])
        if '/runs/' in path:
            return {'status': 'in_progress' if number == 3 else 'completed'}
        if method == 'DELETE':
            if self.fail:
                raise RuntimeError('interrupted')
            self.deleted.append(number)
            self.items.pop(number, None)
            return None
        item = deepcopy(self.items.get(number))
        if self.mismatch and item:
            item['size_in_bytes'] += 1
        return item

class CleanupTests(unittest.TestCase):
    def test_only_completed_old_artifact_is_deleted_and_retry_is_safe(self):
        api = API()
        result = cleanup.prune(api, NOW, True)
        self.assertEqual([1], api.deleted)
        self.assertEqual(60, result['reclaimed_bytes'])
        self.assertEqual({2, 3}, set(api.items))
        self.assertEqual(0, cleanup.prune(api, NOW, True)['deleted'])
    def test_dry_run_preserves_everything(self):
        api = API()
        self.assertEqual(1, cleanup.prune(api, NOW)['candidates'])
        self.assertEqual([], api.deleted)
    def test_snapshot_change_stops_deletion(self):
        api = API()
        api.mismatch = True
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            cleanup.prune(api, NOW, True)
        self.assertEqual([], api.deleted)
    def test_interruption_does_not_report_reclaimed_storage(self):
        api = API()
        api.fail = True
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            cleanup.prune(api, NOW, True)
        self.assertEqual([], api.deleted)
    def test_missing_source_run_fails_before_mutation(self):
        api = API()
        api.items[1].pop('workflow_run')
        with self.assertRaisesRegex(RuntimeError, 'source run'):
            cleanup.prune(api, NOW, True)
        self.assertEqual([], api.deleted)


class ProgressTests(unittest.TestCase):
    def test_request_logs_before_network_call_and_heartbeats_without_disclosing_token(self):
        import io
        import threading
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch

        waiting = threading.Event()
        class Output(io.StringIO):
            def write(self, text):
                if 'waiting for GitHub' in text:
                    waiting.set()
                return super().write(text)
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'{}'
        output, stdout = Output(), io.StringIO()
        def delayed_request(request, timeout):
            self.assertIn('requesting', output.getvalue())
            self.assertEqual(30, timeout)
            self.assertTrue(waiting.wait(2), 'no progress heartbeat while request is waiting')
            return Response()
        with redirect_stderr(output), redirect_stdout(stdout), patch.object(cleanup, 'HEARTBEAT_SECONDS', 0.01), patch.object(cleanup.urllib.request, 'urlopen', side_effect=delayed_request):
            result = cleanup.GitHub('example/project', 'SECRET_TEST_TOKEN').request('/releases')
        self.assertEqual({}, result)
        self.assertIn('waiting for GitHub', output.getvalue())
        self.assertIn('HTTP 200 completed', output.getvalue())
        self.assertNotIn('SECRET_TEST_TOKEN', output.getvalue())
        self.assertEqual('', stdout.getvalue())

    def test_cli_keeps_json_receipt_on_stdout_and_progress_on_stderr(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(stdout), patch.object(cleanup, 'GitHub', return_value=API()), patch.object(cleanup.sys, 'argv', ['cleanup']), patch.dict(cleanup.os.environ, {'GITHUB_REPOSITORY': 'example/project', 'GH_TOKEN': 'SECRET_TEST_TOKEN'}, clear=True):
            cleanup.main()
        receipt = json.loads(stdout.getvalue())
        self.assertFalse(receipt['apply'])
        self.assertIn('Target repository: example/project', stderr.getvalue())
        self.assertIn('DRY RUN', stderr.getvalue())
        self.assertNotIn('SECRET_TEST_TOKEN', stdout.getvalue() + stderr.getvalue())

    def test_timeout_is_visible_without_logging_credentials(self):
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        output = io.StringIO()
        with redirect_stderr(output), patch.object(cleanup.urllib.request, 'urlopen', side_effect=TimeoutError('SECRET_TEST_TOKEN')):
            with self.assertRaises(TimeoutError):
                cleanup.GitHub('example/project', 'SECRET_TEST_TOKEN').request('/releases')
        self.assertIn('failed (TimeoutError)', output.getvalue())
        self.assertNotIn('SECRET_TEST_TOKEN', output.getvalue())


if __name__ == '__main__':
    unittest.main()

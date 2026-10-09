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



class ConfirmationTests(unittest.TestCase):
    def run_cli(self, args, answer='', interactive=True):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        class Terminal(io.StringIO):
            def isatty(self):
                return interactive
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        self.api = API()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(cleanup, 'GitHub', return_value=self.api), patch.object(cleanup, 'datetime', Clock), patch.object(cleanup.sys, 'argv', ['cleanup'] + args), patch.object(cleanup.sys, 'stdin', Terminal(answer)), patch.dict(cleanup.os.environ, {'GITHUB_REPOSITORY': 'example/project', 'GH_TOKEN': 'SECRET_TEST_TOKEN'}, clear=True):
            cleanup.main()
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def test_manual_no_and_eof_cancel_without_deletion(self):
        for answer in ['no\n', '', '\n']:
            receipt, logs = self.run_cli(['--apply'], answer)
            self.assertTrue(receipt['cancelled'])
            self.assertEqual(0, receipt['reclaimed_bytes'])
            self.assertEqual([], self.api.deleted)
            self.assertLess(logs.index('Deletion plan:'), logs.index('Proceed with this deletion plan?'))
            self.assertIn('"total_bytes": 60', logs)

    def test_manual_yes_confirms_before_first_deletion(self):
        receipt, logs = self.run_cli(['--apply'], 'yes\n')
        self.assertEqual([1], self.api.deleted)
        self.assertEqual(60, receipt['reclaimed_bytes'])
        self.assertLess(logs.index('Deletion plan:'), logs.index('Confirmation: yes'))
        self.assertLess(logs.index('Confirmation: yes'), logs.index('Deleting '))

    def test_automation_explicit_yes_does_not_prompt(self):
        receipt, logs = self.run_cli(['--apply', '--yes'], interactive=False)
        self.assertEqual([1], self.api.deleted)
        self.assertIn('Confirmation: yes (--yes supplied)', logs)
        self.assertNotIn('Proceed with this deletion plan?', logs)

    def test_noninteractive_apply_without_yes_fails_before_deletion(self):
        with self.assertRaisesRegex(RuntimeError, 'interactive terminal'):
            self.run_cli(['--apply'], 'yes\n', interactive=False)
        self.assertEqual([], self.api.deleted)

    def test_yes_alone_is_still_a_dry_run(self):
        receipt, logs = self.run_cli(['--yes'], interactive=False)
        self.assertFalse(receipt['apply'])
        self.assertEqual([], self.api.deleted)
        self.assertNotIn('Proceed with this deletion plan?', logs)



class AuthenticationTests(unittest.TestCase):
    def resolve(self, environment, interactive=True, result=None, error=None):
        import io
        from contextlib import redirect_stderr
        from unittest.mock import Mock, patch
        output = io.StringIO()
        terminal = Mock()
        terminal.isatty.return_value = interactive
        with redirect_stderr(output), patch.dict(cleanup.os.environ, environment, clear=True), patch.object(cleanup.sys, 'stdin', terminal), patch.object(cleanup.subprocess, 'run', return_value=result, side_effect=error) as command:
            try:
                token = cleanup.resolve_token()
                return token, output.getvalue(), command
            finally:
                self.command = command
                self.logs = output.getvalue()

    def test_explicit_environment_token_takes_precedence_over_local_login(self):
        token, logs, command = self.resolve({'GH_TOKEN': 'ENV_SECRET', 'GITHUB_TOKEN': 'OTHER_SECRET'}, interactive=False)
        self.assertEqual('ENV_SECRET', token)
        command.assert_not_called()
        self.assertNotIn('ENV_SECRET', logs)
        token, _, command = self.resolve({'GITHUB_TOKEN': 'WORKFLOW_SECRET'}, interactive=False)
        self.assertEqual('WORKFLOW_SECRET', token)
        command.assert_not_called()

    def test_interactive_manual_run_reuses_existing_cli_login_without_disclosure(self):
        from types import SimpleNamespace
        token, logs, command = self.resolve({}, result=SimpleNamespace(returncode=0, stdout='LOCAL_SECRET\n', stderr='PRIVATE_DIAGNOSTIC'))
        self.assertEqual('LOCAL_SECRET', token)
        self.assertEqual(['gh', 'auth', 'token', '--hostname', 'github.com'], command.call_args.args[0])
        self.assertEqual(cleanup.subprocess.DEVNULL, command.call_args.kwargs['stdin'])
        self.assertEqual(15, command.call_args.kwargs['timeout'])
        self.assertNotIn('LOCAL_SECRET', logs)
        self.assertNotIn('PRIVATE_DIAGNOSTIC', logs)

    def test_ci_and_noninteractive_runs_never_consult_local_login(self):
        for environment, interactive in [({}, False), ({'CI': 'true'}, True), ({'GITHUB_ACTIONS': 'true'}, True)]:
            with self.assertRaisesRegex(RuntimeError, 'Set GH_TOKEN'):
                self.resolve(environment, interactive=interactive)
            self.command.assert_not_called()

    def test_missing_failed_or_empty_cli_login_has_clear_sanitized_error(self):
        from types import SimpleNamespace
        for result in [SimpleNamespace(returncode=1, stdout='PRIVATE_OUTPUT', stderr='PRIVATE_ERROR'), SimpleNamespace(returncode=0, stdout='', stderr='')]:
            with self.assertRaisesRegex(RuntimeError, 'No usable local GitHub CLI login') as caught:
                self.resolve({}, result=result)
            self.assertNotIn('PRIVATE_', str(caught.exception) + self.logs)
        with self.assertRaisesRegex(RuntimeError, 'authentication unavailable'):
            self.resolve({}, error=FileNotFoundError('PRIVATE_PATH'))
        self.assertNotIn('PRIVATE_PATH', self.logs)

    def test_cli_timeout_cannot_disclose_captured_credentials(self):
        error = cleanup.subprocess.TimeoutExpired('gh', 15, output='LOCAL_SECRET', stderr='PRIVATE_ERROR')
        with self.assertRaisesRegex(RuntimeError, 'authentication unavailable') as caught:
            self.resolve({}, error=error)
        self.assertNotIn('LOCAL_SECRET', str(caught.exception) + self.logs)
        self.assertNotIn('PRIVATE_ERROR', str(caught.exception) + self.logs)


class StorageReportTests(unittest.TestCase):
    def test_report_counts_live_bytes_sorts_repositories_and_only_issues_gets(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('storage_report', Path(__file__).with_name('report-actions-storage.py'))
        reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reporter)
        requests = []
        def request(api, path, method='GET'):
            requests.append(method)
            if '/bad' in api.prefix:
                raise RuntimeError('unavailable')
            size = 100 if '/large' in api.prefix else 10
            return {'artifacts': [
                {'id': 1, 'size_in_bytes': size, 'created_at': '2026-09-01T00:00:00Z'},
                {'id': 2, 'size_in_bytes': 5, 'created_at': '2026-10-08T00:00:00Z'},
                {'id': 3, 'size_in_bytes': 999, 'created_at': '2026-09-01T00:00:00Z', 'expired': True},
            ]}
        with patch.object(reporter.cleanup.GitHub, 'request', request):
            result = reporter.report(['example/small', 'example/large', 'example/bad'], 'TEST', NOW)
        self.assertEqual(['example/large', 'example/small'], [row['repository'] for row in result['repositories']])
        self.assertEqual(120, result['total_verified_bytes'])
        self.assertEqual(100, result['repositories'][0]['older_than_five_days_bytes'])
        self.assertEqual(2, result['repositories'][0]['artifact_count'])
        self.assertEqual(1, len(result['errors']))
        self.assertEqual({'GET'}, set(requests))

    def test_report_paginates_organization_and_rejects_incomplete_inventory(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('storage_report', Path(__file__).with_name('report-actions-storage.py'))
        reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reporter)
        def request(api, path, method='GET'):
            self.assertEqual('https://api.github.com/orgs/example', api.prefix)
            self.assertEqual('GET', method)
            return [{'full_name': f'example/repo{i}'} for i in range(100)] if path.endswith('&page=1') else [{'full_name':'example/last'}]
        with patch.object(reporter.cleanup.GitHub, 'request', request):
            self.assertEqual(101, len(reporter.repositories('example', 'TEST')))
        with patch.object(reporter.cleanup.GitHub, 'request', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                reporter.repositories('example', 'TEST')


if __name__ == '__main__':
    unittest.main()

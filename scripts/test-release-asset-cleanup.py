#!/usr/bin/env python3
"""Exercise the 14-day policy through the actual release cleanup path."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('release_cleanup', Path(__file__).resolve().parents[1] / 'consumers/files/.shared/cleanup-release-assets.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

class API:
    def __init__(self):
        self.releases = [
            {'id': i, 'tag_name': tag, 'published_at': date, 'draft': False, 'prerelease': pre}
            for i, tag, date, pre in [
                (1, 'v1.0.0', '2026-09-01T00:00:00Z', False),
                (2, 'v2.0.0', '2026-09-02T00:00:00Z', False),
                (3, 'v3.0.0', '2026-09-30T00:00:00Z', False),
                (4, 'shared-v1.0.0', '2026-09-01T00:00:00Z', False),
                (5, 'v4.0.0-rc.1', '2026-09-01T00:00:00Z', True),
            ]
        ]
        self.assets = {i: {'id': i, 'name': 'bundle', 'size': 60, 'created_at': '2026-09-01T00:00:00Z', 'updated_at': '2026-09-01T00:00:00Z', 'state': 'uploaded'} for i in range(1, 6)}
        self.latest = 2  # Explicit current release differs from the newest publication.
        self.deleted = []
        self.mismatch = False
        self.rollback = False
        self.inventories = 0
    def inventory(self, path):
        if path == '/releases':
            self.inventories += 1
            if self.rollback and self.inventories > 1:
                self.latest = 1
            return deepcopy(self.releases)
        release_id = int(path.split('/')[2])
        return [deepcopy(self.assets[release_id])] if release_id in self.assets else []
    def request(self, path, method='GET'):
        if path == '/releases/latest':
            return deepcopy(next(r for r in self.releases if r['id'] == self.latest))
        i = int(path.rsplit('/', 1)[1])
        if method == 'DELETE':
            self.deleted.append(i)
            self.assets.pop(i, None)
            return None
        result = deepcopy(self.assets.get(i))
        if self.mismatch and result:
            result['size'] += 1
        return result

class CleanupTests(unittest.TestCase):
    def test_superseded_old_asset_deleted_current_and_component_releases_preserved(self):
        api = API()
        result = cleanup.prune(api, NOW, apply=True)
        self.assertEqual([1], api.deleted)
        self.assertEqual(60, result['reclaimed_bytes'])
        self.assertEqual({2, 3, 4, 5}, set(api.assets))
        self.assertEqual([], cleanup.prune(api, NOW, apply=True)['deleted_asset_ids'])
    def test_protected_in_use_version_preserved(self):
        api = API()
        cleanup.prune(api, NOW, protected_tags=['v1.0.0'], apply=True)
        self.assertEqual([], api.deleted)
    def test_dry_run_has_no_mutations(self):
        api = API()
        self.assertEqual(1, cleanup.prune(api, NOW)['candidates'])
        self.assertEqual([], api.deleted)
    def test_recently_replaced_asset_preserved(self):
        api = API()
        api.assets[1]['updated_at'] = '2026-10-01T00:00:00Z'
        cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
    def test_asset_snapshot_change_stops_deletion(self):
        api = API()
        api.mismatch = True
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
    def test_release_promoted_during_cleanup_preserved(self):
        api = API()
        api.rollback = True
        cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
    def test_unknown_tag_and_draft_preserved(self):
        api = API()
        api.releases[0]['tag_name'] = 'unversioned-build'
        cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
        api = API()
        api.releases[0]['draft'] = True
        cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
    def test_immutable_release_is_reported_and_preserved(self):
        api = API()
        api.releases[0]['immutable'] = True
        result = cleanup.prune(api, NOW, apply=True)
        self.assertEqual([], api.deleted)
        self.assertEqual([1], result['immutable_release_ids'])

    def test_release_and_asset_pagination_are_complete(self):
        api = cleanup.GitHub('example/project', 'test')
        calls = []
        def request(path, method='GET'):
            calls.append(path)
            return [{'id': i} for i in range(100)] if path.endswith('&page=1') else [{'id': 100}]
        api.request = request
        self.assertEqual(101, len(api.inventory('/releases/1/assets')))
        self.assertEqual(['/releases/1/assets?per_page=100&page=1', '/releases/1/assets?per_page=100&page=2'], calls)


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


if __name__ == '__main__':
    unittest.main()

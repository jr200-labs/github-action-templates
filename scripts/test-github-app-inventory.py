#!/usr/bin/env python3
"""Regressions for permission drift, pagination and credential-free validation."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import urllib.error

spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'actions/audit-github-apps/audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

APP = {'slug': 'example-audit', 'app_id': 123, 'repository_selection': 'all', 'permissions': {'checks': 'read'}, 'events': []}


class InventoryTests(unittest.TestCase):
    def test_missing_checks_fails_and_approved_grant_passes(self):
        live = {'example-audit': dict(APP, permissions={})}
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, audit.audit([APP], live))
        self.assertIn('permission checks: absent -> read', output.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, audit.audit([APP], {'example-audit': APP}))

    def test_missing_suspended_and_overprivileged_installations_fail(self):
        self.assertEqual(['missing installation'], audit.differences(APP, None))
        changed = dict(APP, permissions={'checks': 'write', 'contents': 'write'}, suspended_at='2026-01-01', events=['push'])
        problems = audit.differences(APP, changed)
        self.assertIn('installation is suspended', problems)
        self.assertIn('permission checks: write -> read', problems)
        self.assertIn('permission contents: write -> absent', problems)
        self.assertIn('event remove push', problems)

    def test_all_pages_are_checked(self):
        first = [dict(APP, app_slug=f'example-{i}') for i in range(100)]
        with patch.object(audit, 'request_json', side_effect=[{'installations': first}, {'installations': [dict(APP, app_slug='last')]}]) as request:
            result = audit.installations('example-org', 'private-token')
        self.assertEqual(101, len(result))
        self.assertIn('page=2', request.call_args.args[0])

    def test_auth_failure_is_actionable_and_private(self):
        error = urllib.error.HTTPError('https://api.github.com', 403, 'Forbidden', {}, io.BytesIO(b'sensitive-upstream-body'))
        with patch.object(audit.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'Administration read') as caught:
                audit.installations('example-org', 'private-token')
        self.assertNotIn('private-token', str(caught.exception))
        self.assertNotIn('sensitive-upstream-body', str(caught.exception))

    def test_validation_never_uses_network_or_credentials_and_rejects_wrong_org(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inventory.json'
            path.write_text(json.dumps({'version': 1, 'organization': 'example-org', 'apps': [APP]}))
            with patch.object(audit, 'request_json') as request, patch.object(audit.sys, 'argv', ['audit.py', str(path), 'example-org', 'true']), redirect_stdout(io.StringIO()):
                self.assertEqual(0, audit.main())
            request.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'organization must match'):
                audit.load_inventory(path, 'different-org')
            path.write_text(json.dumps({'version': 1, 'organization': 'example-org', 'apps': []}))
            with self.assertRaisesRegex(ValueError, 'nonempty'):
                audit.load_inventory(path, 'example-org')

    def test_device_codes_are_never_printed(self):
        expected = dict(APP, device_flow_required=True, client_id='example-client')
        with patch.object(audit, 'request_json', return_value={'device_code': 'secret-device', 'user_code': 'secret-user'}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, audit.audit([expected], {'example-audit': APP}))
        self.assertNotIn('secret', output.getvalue())
        with patch.object(audit, 'request_json', return_value={'error': 'device_flow_disabled'}):
            self.assertEqual(['device flow is disabled'], audit.device_flow_problems(expected))


if __name__ == '__main__':
    unittest.main()

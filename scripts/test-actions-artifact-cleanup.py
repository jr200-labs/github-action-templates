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

if __name__ == '__main__':
    unittest.main()

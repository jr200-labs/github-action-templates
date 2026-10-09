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

if __name__ == '__main__':
    unittest.main()

"""Real Git identities and immutable archive promotion without a native build."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verified_build', ROOT / 'actions/macos-verified-build/reuse.py')
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


class VerifiedBuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        previous = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, previous)
        subprocess.run(['git', 'init', '-q', '-b', 'master'], check=True)
        Path('source').write_text('original')
        Path('.github').mkdir(exist_ok=True)
        Path('.github/macos-verification-inputs.json').write_text(json.dumps([sys.executable, '-c', 'print(\'{"compiler":"fixture-v1"}\')']))
        subprocess.run(['git', 'add', 'source', '.github'], check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'test: fixture'], check=True)
        subprocess.run(['git', 'tag', 'v1.2.3'], check=True)
        self.config = dict(artifact='example-macos', app='dist/Example.app', build=['build'])
        self.env = dict(GITHUB_EVENT_NAME='repository_dispatch', GITHUB_REPOSITORY='example/app')
        self.output, self.cache = self.root / 'output', self.root / 'cache'
        self.key = reuse.identity(self.config, 'v1.2.3', self.env)

    def archive(self):
        self.output.mkdir()
        archive = self.output / 'example-macos.zip'
        archive.write_bytes(b'original fully verified signed bytes')
        (self.output / 'example-macos.zip.sha256').write_text(reuse.digest(archive) + '  example-macos.zip\n')

    def test_publication_retry_promotes_identical_bytes_without_invoking_build(self):
        self.archive()
        original = (self.output / 'example-macos.zip').read_bytes()
        reuse.save(self.config, self.output, self.cache, self.key, 'v1.2.3', '123')
        shutil.rmtree(self.output)
        with patch.object(reuse.subprocess, 'run', side_effect=AssertionError('unexpected build')):
            self.assertTrue(reuse.restore(self.config, self.output, self.cache, self.key, 'v1.2.3'))
        self.assertEqual((self.output / 'example-macos.zip').read_bytes(), original)
        self.assertFalse((self.output / 'verification.json').exists())

    def test_input_changes_dirty_sources_and_untrusted_events_cannot_reuse(self):
        self.assertEqual(reuse.identity(self.config, 'v1.2.3', self.env), self.key)
        original = reuse.command
        def changed(command):
            return '{"compiler":"fixture-v2"}' if command[0] == sys.executable else original(command)
        with patch.object(reuse, 'command', side_effect=changed):
            self.assertNotEqual(reuse.identity(self.config, 'v1.2.3', self.env), self.key)
        for event in ('pull_request', 'pull_request_target', 'push'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'trusted release'):
                reuse.identity(self.config, 'v1.2.3', dict(self.env, GITHUB_EVENT_NAME=event))
        Path('source').write_text('edited')
        with self.assertRaisesRegex(ValueError, 'clean source'):
            reuse.identity(self.config, 'v1.2.3', self.env)
        subprocess.run(['git', 'add', 'source'], check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'test: changed'], check=True)
        with self.assertRaisesRegex(ValueError, 'exact release tag'):
            reuse.identity(self.config, 'v1.2.3', self.env)

    def test_missing_failed_and_corrupt_outputs_never_become_verified(self):
        self.assertFalse(reuse.restore(self.config, self.output, self.cache, self.key, 'v1.2.3'))
        with self.assertRaisesRegex(ValueError, 'Missing verified'):
            reuse.save(self.config, self.output, self.cache, self.key, 'v1.2.3', '123')
        self.assertFalse(self.cache.exists())
        self.archive()
        reuse.save(self.config, self.output, self.cache, self.key, 'v1.2.3', '123')
        shutil.rmtree(self.output)
        (self.cache / 'example-macos.zip').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            reuse.restore(self.config, self.output, self.cache, self.key, 'v1.2.3')
        self.assertFalse(self.output.exists())

    def test_receipt_tag_key_expiry_and_symlinks_are_checked(self):
        self.archive()
        reuse.save(self.config, self.output, self.cache, self.key, 'v1.2.3', '123')
        shutil.rmtree(self.output)
        receipt_path = self.cache / 'verification.json'
        original = receipt_path.read_text()
        for change in ({'tag': 'v9.9.9'}, {'key': 'other'}, {'verified_at': 0}, {'schema': 9}):
            receipt_path.write_text(json.dumps(dict(json.loads(original), **change)))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'receipt'):
                reuse.restore(self.config, self.output, self.cache, self.key, 'v1.2.3')
            self.assertFalse(self.output.exists())
        receipt_path.unlink()
        external = self.root / 'receipt'
        external.write_text(original)
        receipt_path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'regular'):
            reuse.restore(self.config, self.output, self.cache, self.key, 'v1.2.3')

    def test_action_entrypoint_reuses_across_runner_slots_and_misses_changed_inputs(self):
        self.archive()
        config = self.root / 'app.json'
        config.write_text(json.dumps(self.config))
        first, second = self.root / 'runner-one', self.root / 'runner-two'
        first.mkdir()
        second.mkdir()
        self.output.rename(first / 'macos-release')
        result = self.root / 'github-output'
        environment = dict(os.environ, **self.env, GITHUB_RUN_ID='123', HOME=str(self.root),
                           RUNNER_TEMP=str(first), REUSE_CONFIG=str(config), REUSE_TAG='v1.2.3',
                           REUSE_KEY=self.key, GITHUB_OUTPUT=str(result), REUSE_MODE='save')
        subprocess.run([sys.executable, str(ROOT / 'actions/macos-verified-build/reuse.py')], env=environment, check=True, capture_output=True)
        shutil.rmtree(first)
        environment.update(RUNNER_TEMP=str(second), REUSE_MODE='restore')
        subprocess.run([sys.executable, str(ROOT / 'actions/macos-verified-build/reuse.py')], env=environment, check=True, capture_output=True)
        self.assertIn('reused=true', result.read_text())
        self.assertEqual((second / 'macos-release/example-macos.zip').read_bytes(), b'original fully verified signed bytes')
        shutil.rmtree(second / 'macos-release')
        result.write_text('')
        environment['REUSE_KEY'] = 'macos-verified-v1-' + 'f' * 64
        subprocess.run([sys.executable, str(ROOT / 'actions/macos-verified-build/reuse.py')], env=environment, check=True, capture_output=True)
        self.assertIn('reused=false', result.read_text())
        self.assertFalse((second / 'macos-release').exists())

    def test_local_retry_survives_new_job_workspace_and_rejects_other_repository(self):
        self.archive()
        environment = dict(self.env, GITHUB_RUN_ID='123')
        with patch.object(reuse.Path, 'home', return_value=self.root):
            self.assertTrue(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment))
            fresh = self.root / 'new-job'
            fresh.mkdir()
            output = fresh / 'release'
            with patch.object(reuse, 'save', side_effect=AssertionError('retry must not save')):
                self.assertTrue(reuse.local_operation(self.config, output, self.key, 'v1.2.3', 'restore', environment))
            self.assertEqual((output / 'example-macos.zip').read_bytes(), (self.output / 'example-macos.zip').read_bytes())
            other_output = self.root / 'other-output'
            self.assertFalse(reuse.local_operation(self.config, other_output, self.key, 'v1.2.3', 'restore', dict(environment, GITHUB_REPOSITORY='other/app')))
            self.assertFalse(other_output.exists())
            with self.assertRaisesRegex(ValueError, 'trusted release'):
                reuse.local_operation(self.config, other_output, self.key, 'v1.2.3', 'restore', dict(environment, GITHUB_EVENT_NAME='pull_request'))

    def test_invalid_local_entry_rebuilds_and_can_be_replaced(self):
        self.archive()
        environment = dict(self.env, GITHUB_RUN_ID='123')
        with patch.object(reuse.Path, 'home', return_value=self.root):
            self.assertTrue(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment))
            with reuse.local_store(self.env['GITHUB_REPOSITORY'], self.key) as cache:
                (cache / 'example-macos.zip').write_bytes(b'corrupt')
            output = self.root / 'retry'
            self.assertFalse(reuse.local_operation(self.config, output, self.key, 'v1.2.3', 'restore', environment))
            self.assertFalse(output.exists())
            self.assertTrue(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment))
            self.assertTrue(reuse.local_operation(self.config, output, self.key, 'v1.2.3', 'restore', environment))

    def test_nonobject_receipt_and_unsafe_root_fall_back_without_publishing(self):
        self.archive()
        environment = dict(self.env, GITHUB_RUN_ID='123')
        with patch.object(reuse.Path, 'home', return_value=self.root):
            reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment)
            with reuse.local_store(self.env['GITHUB_REPOSITORY'], self.key) as cache:
                (cache / 'verification.json').write_text('[]')
            retry = self.root / 'retry'
            self.assertFalse(reuse.local_operation(self.config, retry, self.key, 'v1.2.3', 'restore', environment))
            self.assertFalse(retry.exists())
            shutil.rmtree(self.root / '.cache')
            external = self.root / 'external'
            external.mkdir()
            (self.root / '.cache').symlink_to(external, target_is_directory=True)
            self.assertFalse(reuse.local_operation(self.config, retry, self.key, 'v1.2.3', 'save', environment))
            self.assertEqual(list(external.iterdir()), [])

    def test_busy_or_unavailable_local_store_does_not_block_release(self):
        environment = dict(self.env, GITHUB_RUN_ID='123')
        with patch.object(reuse.Path, 'home', return_value=self.root):
            with reuse.local_store(self.env['GITHUB_REPOSITORY'], self.key):
                self.assertFalse(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'restore', environment))
                self.assertFalse(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment))
            with patch.object(reuse, 'private_directory', side_effect=OSError('unavailable')):
                self.assertFalse(reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'restore', environment))
            self.assertFalse(self.output.exists())

    def test_local_store_prunes_expired_and_excess_archives_without_following_links(self):
        self.archive()
        environment = dict(self.env, GITHUB_RUN_ID='123')
        with patch.object(reuse.Path, 'home', return_value=self.root):
            reuse.local_operation(self.config, self.output, self.key, 'v1.2.3', 'save', environment)
            with reuse.local_store(self.env['GITHUB_REPOSITORY'], self.key) as cache:
                root = cache.parent
                expired = root / ('macos-verified-v1-' + 'a' * 64)
                shutil.copytree(cache, expired)
                os.utime(expired, (0, 0))
                linked = root / ('macos-verified-v1-' + 'b' * 64)
                linked.symlink_to(self.output, target_is_directory=True)
                partial = root / '.partial-abandoned'
                partial.mkdir()
                reuse.prune(root)
                self.assertFalse(expired.exists())
                self.assertFalse(linked.is_symlink())
                self.assertTrue(self.output.exists())
                self.assertFalse(partial.exists())
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                with patch.object(reuse, 'MAX_BYTES', 1):
                    reuse.prune(root)
                self.assertFalse(cache.exists())
            self.assertFalse(reuse.local_operation(self.config, self.root / 'cold', self.key, 'v1.2.3', 'restore', environment))

    def test_workflow_only_saves_successful_release_builds_before_publication(self):
        workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/build_macos_app.yaml')], text=True))
        steps = workflow['jobs']['package']['steps']
        by_name = {s.get('name'): s for s in steps}
        restore = by_name['Restore local verified release archive']
        self.assertIn('inputs.publish-release', restore['if'])
        self.assertNotIn('actions/cache', json.dumps(steps))
        self.assertEqual(restore['with']['mode'], 'restore')
        self.assertEqual(by_name['Save local verified release before publication']['with']['mode'], 'save')
        save = by_name['Save local verified release before publication']
        self.assertIn('inputs.publish-release', save['if'])
        self.assertNotIn('always()', save['if'])
        self.assertLess(steps.index(by_name['Build and verify']), steps.index(save))
        self.assertLess(steps.index(save), steps.index(by_name['Generate signed Sparkle appcast']))
        self.assertLess(steps.index(save), steps.index(by_name['Verify and attach release assets']))
        self.assertNotIn('SPARKLE_EDDSA_PRIVATE_KEY', json.dumps(restore))


if __name__ == '__main__':
    unittest.main()

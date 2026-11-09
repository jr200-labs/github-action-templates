"""Source and input receipts shared by verification and packaging jobs."""
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
        Path('.github/macos-package-inputs.json').write_text(json.dumps([sys.executable, '-c', 'import json; print(json.dumps(dict(compiler="fixture-mac")))']))
        subprocess.run(['git', 'add', 'source', '.github'], check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'test: fixture'], check=True)
        subprocess.run(['git', 'tag', 'v1.2.3'], check=True)
        self.config = dict(artifact='example-macos', app='dist/Example.app', build=['build'], verify=['verify'])
        self.env = dict(GITHUB_EVENT_NAME='repository_dispatch', GITHUB_REPOSITORY='example/app')
        self.key = reuse.identity(self.config, 'v1.2.3', self.env)


    def test_input_changes_and_dirty_sources_change_verification_identity(self):
        self.assertEqual(reuse.identity(self.config, 'v1.2.3', self.env), self.key)
        original = reuse.command
        def changed(command):
            return '{"compiler":"fixture-v2"}' if command[0] == sys.executable else original(command)
        with patch.object(reuse, 'command', side_effect=changed):
            self.assertNotEqual(reuse.identity(self.config, 'v1.2.3', self.env), self.key)
        Path('source').write_text('edited')
        with self.assertRaisesRegex(ValueError, 'clean source'):
            reuse.identity(self.config, 'v1.2.3', self.env)
        subprocess.run(['git', 'add', 'source'], check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'test: changed'], check=True)
        with self.assertRaisesRegex(ValueError, 'exact release tag'):
            reuse.identity(self.config, 'v1.2.3', self.env)


    def test_stage_receipt_binds_source_inputs_run_and_execution_budget(self):
        env = dict(self.env, GITHUB_RUN_ID='123', REUSE_SPENT='45')
        receipt = self.root / 'validation'
        reuse.validation(self.config, receipt, self.key, 'v1.2.3', env, save=True)
        reuse.validation(self.config, receipt, self.key, 'v1.2.3', env)
        with patch.object(reuse, 'identity', side_effect=AssertionError('verification/simulator probe in packaging')):
            reuse.validation(self.config, receipt, self.key, 'v1.2.3', env)
        with patch.object(reuse, 'package_identity', return_value='different-toolchain'):
            with self.assertRaisesRegex(ValueError, 'build inputs'):
                reuse.validation(self.config, receipt, self.key, 'v1.2.3', env)
        for changed in ({'GITHUB_RUN_ID':'999'}, {'REUSE_SPENT':'60'}):
            with self.assertRaisesRegex(ValueError, 'receipt'):
                reuse.validation(self.config, receipt, self.key, 'v1.2.3', dict(env, **changed))
        for spent in ('-1', 'nan', '1200'):
            with self.assertRaisesRegex(ValueError, 'execution'):
                reuse.validation(self.config, receipt, self.key, 'v1.2.3', dict(env, REUSE_SPENT=spent))

    def test_verification_receipt_can_cross_clean_job_workspaces(self):
        env = dict(self.env, GITHUB_RUN_ID='123', REUSE_SPENT='30')
        receipt = self.root / 'validation'
        reuse.validation(self.config, receipt, self.key, 'v1.2.3', env, save=True)
        second = self.root / 'second-checkout'
        shutil.copytree(self.repo, second)
        try:
            os.chdir(second)
            with patch.object(reuse, 'identity', side_effect=AssertionError('native/simulator verification repeated')):
                reuse.validation(self.config, receipt, self.key, 'v1.2.3', env)
        finally:
            os.chdir(self.repo)


if __name__ == '__main__':
    unittest.main()

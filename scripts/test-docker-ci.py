"""Exercise the distributed verifier through real sync and fake Docker."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerCI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.github').mkdir()
        (self.root / '.github/.shared-config.yaml').write_text('ref: fixture\nworkflows: [docker-ci]\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.env = dict(os.environ, SYNC_BASE_URL=(ROOT / 'consumers').as_uri(),
                        SYNC_SHARED_BASE_URL=(ROOT / 'shared').as_uri(), STRICT='1')
        self.sync()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        tool = self.bin / 'docker'
        tool.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'build' and os.environ.get('FAIL_BUILD'):
    sys.exit(17)
''')
        tool.chmod(0o755)
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ['PATH'], CALLS=str(self.log))

    def sync(self, *args):
        return subprocess.run(['bash', str(ROOT / 'consumers/scripts/sync-shared'), *args],
                              cwd=self.root, env=self.env, capture_output=True, text=True, check=True)

    def run_verifier(self):
        return subprocess.run(['python3', '.shared/check-docker-images.py'], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def catalog(self, entries):
        (self.root / '.github/images.yaml').write_text(json.dumps({'images': entries}))

    def test_group_sync_and_check_install_canonical_files(self):
        self.sync('--check')
        for relative, source in [('.github/workflows/ci-docker.yaml', 'consumers/workflows/ci-docker.yaml'),
                                 ('.shared/check-docker-images.py', 'consumers/files/.shared/check-docker-images.py')]:
            self.assertEqual((self.root / relative).read_bytes(), (ROOT / source).read_bytes())
        self.assertFalse(list((self.root / '.github/workflows').glob('bespoke*')))

    def test_default_build_and_cleanup_without_publication(self):
        (self.root / 'Dockerfile').touch()
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)
        build, cleanup = self.calls()
        self.assertEqual(build[:2], ['build', '--tag'])
        self.assertEqual(build[3:], ['--file', 'Dockerfile', '.'])
        self.assertEqual(cleanup, ['image', 'rm', build[2]])

    def test_nested_default_dockerfile(self):
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0][3:], ['--file', 'docker/Dockerfile', '.'])

    def test_multiple_images_contexts_and_literal_smoke_arguments(self):
        smoke = self.root / 'smoke'
        smoke.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                         'with open(os.environ["CALLS"], "a") as out: out.write(json.dumps(["smoke", *sys.argv[1:]]) + "\\n")\n')
        smoke.chmod(0o755)
        self.catalog([{'dockerfile': 'images/one/Dockerfile', 'context': 'images/one',
                       'build_contexts': 'base=docker-image://example/base:1\n',
                       'smoke-test': ['./smoke', '$(touch unexpected)']}, {'dockerfile': 'two/Dockerfile'}])
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)
        first, assertion, cleanup, second, second_cleanup = self.calls()
        self.assertEqual(first[3:], ['--file', 'images/one/Dockerfile', '--build-context',
                                    'base=docker-image://example/base:1', 'images/one'])
        self.assertEqual(assertion, ['smoke', '$(touch unexpected)', first[2]])
        self.assertEqual(cleanup, ['image', 'rm', first[2]])
        self.assertEqual(second_cleanup, ['image', 'rm', second[2]])
        self.assertNotEqual(first[2], second[2])
        self.assertFalse((self.root / 'unexpected').exists())

    def test_failed_build_stops_before_smoke_and_cleans_up(self):
        self.catalog([{'smoke-test': ['./missing-smoke']}])
        self.env['FAIL_BUILD'] = '1'
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exit status 17', result.stderr)
        build, cleanup = self.calls()
        self.assertEqual(cleanup, ['image', 'rm', build[2]])

    def test_failed_smoke_fails_check_and_cleans_up(self):
        self.catalog([{'smoke-test': ['/bin/sh', '-c', 'exit 23']}])
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exit status 23', result.stderr)
        build, cleanup = self.calls()
        self.assertEqual(cleanup, ['image', 'rm', build[2]])

    def test_invalid_later_entry_fails_before_any_build(self):
        self.catalog([{}, {'smoke-test': 'echo not-an-argv'}])
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('smoke-test must be a nonempty argv array', result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()

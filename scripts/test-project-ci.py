"""Exercise shared project lanes with fake toolchains and real workflow shell."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows' / name)], text=True))


class ProjectCI(unittest.TestCase):
    def test_canonical_groups_resolve_through_real_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github').mkdir()
            (root / '.github/.shared-config.yaml').write_text('ref: fixture\nworkflows:\n  - rust\n  - swift-package\n  - ios-app\n')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            env = dict(os.environ, SYNC_BASE_URL=(ROOT / 'consumers').as_uri(),
                       SYNC_SHARED_BASE_URL=(ROOT / 'shared').as_uri(), STRICT='1')
            subprocess.run(['bash', str(ROOT / 'consumers/scripts/sync-shared')], cwd=root, env=env, check=True, capture_output=True)
            for name in ('ci-rust', 'ci-swift-package', 'ci-ios-app'):
                self.assertEqual((root / f'.github/workflows/{name}.yaml').read_bytes(),
                                 (ROOT / f'consumers/workflows/{name}.yaml').read_bytes())
            self.assertFalse(list((root / '.github/workflows').glob('bespoke*')))

    def test_swift_helper_failure_cannot_fall_back_to_unverified_tests(self):
        script = workflow('ci_swift_package.yaml')['jobs']['check']['steps'][-1]['run']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/swift-package-verify.py').write_text('raise SystemExit(42)\n')
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 42, result.stderr)

    def test_plain_swift_package_runs_build_and_tests(self):
        script = workflow('ci_swift_package.yaml')['jobs']['check']['steps'][-1]['run']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            swift = root / 'swift'
            swift.write_text('#!/bin/sh\necho "$*" >> "$CALLS"\n')
            swift.chmod(0o700)
            calls = root / 'calls'
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], CALLS=str(calls))
            subprocess.run(['bash', '-c', script], cwd=root, env=env, check=True)
            self.assertEqual(calls.read_text().splitlines(), ['build -Xswiftc -warnings-as-errors', 'test'])

    def test_failed_xcode_generation_stops_before_project_resolution(self):
        steps = workflow('ci_swift.yaml')['jobs']['build-and-test']['steps']
        generate = next(step for step in steps if step.get('name') == 'Generate Xcode project')
        self.assertLess(steps.index(generate), next(i for i, step in enumerate(steps) if step.get('id') == 'xcode'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / 'xcodegen'
            tool.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo "Version: 2.46.0"; else exit 19; fi\n')
            tool.chmod(0o700)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'])
            result = subprocess.run(['bash', '-c', generate['run']], cwd=root, env=env, capture_output=True)
            self.assertEqual(result.returncode, 19, result.stderr)


if __name__ == '__main__':
    unittest.main()

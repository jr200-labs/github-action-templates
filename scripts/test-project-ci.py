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
    def test_linter_binary_caches_skip_install_on_hits_and_save_before_lint(self):
        for file, job, tool, cache_id, version, lint in (
            ('ci.yaml', 'validate-workflow-syntax', 'actionlint', 'actionlint-cache', 'v1.7.12', 'Run actionlint'),
            ('ci_go.yaml', 'test', 'golangci-lint', 'golangci-cache', 'v2.15.0', 'Lint'),
        ):
            with self.subTest(tool=tool):
                steps = workflow(file)['jobs'][job]['steps']
                cache = next(step for step in steps if step.get('id') == cache_id)
                install = next(step for step in steps if step.get('name') == f'Install {tool}')
                verify = next(step for step in steps if step.get('name') == f'Verify {tool} binary')
                save = next(step for step in steps if step.get('name') == f'Save {tool} binary')
                self.assertEqual(cache['uses'], 'actions/cache/restore@v6')
                self.assertNotIn('restore-keys', cache['with'])
                for component in ('runner.os', 'runner.arch', version):
                    self.assertIn(component, cache['with']['key'])
                if tool == 'golangci-lint':
                    self.assertIn('steps.go.outputs.go-version', cache['with']['key'])
                    self.assertEqual(next(s for s in steps if s.get('name') == lint)['with']['install-mode'], 'none')
                self.assertEqual(install['if'], f"steps.{cache_id}.outputs.cache-hit != 'true'")
                self.assertEqual(save['if'], install['if'])
                self.assertEqual(save['with']['path'], cache['with']['path'])
                self.assertEqual(save['with']['key'], '${{ steps.' + cache_id + '.outputs.cache-primary-key }}')
                self.assertLess(steps.index(cache), steps.index(install))
                self.assertLess(steps.index(install), steps.index(verify))
                self.assertLess(steps.index(verify), steps.index(save))
                self.assertLess(steps.index(save), next(i for i, s in enumerate(steps) if s.get('name') == lint))
                # Run the real warm-cache verification shell with only the
                # restored executable; no Go compiler or downloader is invoked.
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    binary_dir = root / tool / 'bin'
                    binary_dir.mkdir(parents=True)
                    binary = binary_dir / tool
                    binary.write_text('#!/bin/sh\necho cached-tool\n')
                    binary.chmod(0o755)
                    output = root / 'output'
                    path = root / 'path'
                    env = dict(os.environ, GITHUB_OUTPUT=str(output), GITHUB_PATH=str(path))
                    for key, value in verify['env'].items():
                        env[key] = value.replace('${{ runner.temp }}', str(root))
                    subprocess.run(['bash', '-c', verify['run']], env=env, check=True, capture_output=True)
                    published = output if tool == 'actionlint' else path
                    self.assertIn(str(binary_dir), published.read_text())

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

"""Run the shared workflow's actual setup guards with missing/broken tools."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CocogittoInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = json.loads(subprocess.check_output([
            'yq', '-o=json', str(ROOT / '.github/workflows/lint_commits.yaml')], text=True))
        cls.steps = workflow['jobs']['commitlint']['steps']
        cls.by_name = {step['name']: step for step in cls.steps}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.output = self.root / 'output'
        # These two guards only use Bash builtins and the fixture cog. Exclude
        # the host PATH so a locally installed real cog cannot mask failures.
        self.env = dict(os.environ, PATH=str(self.bin), GITHUB_OUTPUT=str(self.output))

    def tool(self, code=0):
        path = self.bin / 'cog'
        path.write_text(f'#!/bin/bash\necho "cog fixture"\nexit {code}\n')
        path.chmod(0o755)

    def run_step(self, name):
        return subprocess.run(['/bin/bash', '-c', self.by_name[name]['run']],
                              env=self.env, text=True, capture_output=True)

    def test_missing_binary_requests_retry_and_a_successful_retry_recovers(self):
        checked = self.run_step('Check Cocogitto installation')
        self.assertEqual(checked.returncode, 0)
        self.assertEqual(self.output.read_text(), 'ready=false\n')
        self.tool()
        self.assertEqual(self.run_step('Verify Cocogitto installation').returncode, 0)

    def test_working_install_skips_the_retry(self):
        self.tool()
        checked = self.run_step('Check Cocogitto installation')
        self.assertEqual(checked.returncode, 0)
        self.assertEqual(self.output.read_text(), 'ready=true\n')
        self.assertEqual(self.by_name['Retry incomplete Cocogitto installation']['if'],
                         "steps.cocogitto.outputs.ready != 'true'")
        self.assertEqual(self.run_step('Verify Cocogitto installation').returncode, 0)

    def test_persistently_missing_binary_fails_at_setup(self):
        self.run_step('Check Cocogitto installation')
        result = self.run_step('Verify Cocogitto installation')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('after two attempts', result.stdout)

    def test_present_but_broken_binary_requests_retry_and_cannot_pass_verification(self):
        self.tool(37)
        self.assertEqual(self.run_step('Check Cocogitto installation').returncode, 0)
        self.assertEqual(self.output.read_text(), 'ready=false\n')
        self.assertEqual(self.run_step('Verify Cocogitto installation').returncode, 37)

    def test_setup_is_bounded_and_validation_remains_mandatory(self):
        first = self.by_name['Install Cocogitto']
        retry = self.by_name['Retry incomplete Cocogitto installation']
        self.assertTrue(first['continue-on-error'])
        self.assertEqual(first['uses'], retry['uses'])
        self.assertTrue(first['with']['install-only'])
        self.assertTrue(retry['with']['install-only'])
        self.assertNotIn('continue-on-error', retry)
        verify = self.by_name['Verify Cocogitto installation']
        self.assertNotIn('continue-on-error', verify)
        self.assertNotIn('if', verify)
        names = [step['name'] for step in self.steps]
        self.assertLess(names.index('Verify Cocogitto installation'), names.index('Verify PR title'))
        self.assertLess(names.index('Verify Cocogitto installation'), names.index('Check commits'))
        self.assertEqual(sum(step.get('uses', '').startswith('cocogitto/cocogitto-action@') for step in self.steps), 2)


if __name__ == '__main__':
    unittest.main()

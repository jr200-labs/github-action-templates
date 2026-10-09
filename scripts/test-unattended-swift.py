"""Exercise the real workflow resolver without Xcode or a simulator."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UnattendedResolutionTest(unittest.TestCase):
    def test_protected_lane_does_not_create_a_simulator_before_acceptance(self):
        workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/ci_swift.yaml')], text=True))
        steps = workflow['jobs']['build-and-test']['steps']
        resolve = next(step for step in steps if step.get('id') == 'xcode')
        # GitHub renders run identity expressions before invoking the shell.
        script = re.sub(r'\$\{\{ github\.(run_id|run_attempt|job) \}\}', 'fixture', resolve['run'])
        for key, value in dict(project='Fixture.xcodeproj', scheme='Fixture', destination='', configuration='Debug').items():
            script = script.replace('${{ inputs.' + key + ' }}', value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Fixture.xcodeproj').mkdir()
            binary = root / 'bin'
            binary.mkdir()
            xcode = binary / 'xcodebuild'
            xcode.write_text('#!/bin/sh\nprintf "    SUPPORTED_PLATFORMS = iphonesimulator iphoneos\\n"\n')
            xcode.chmod(0o700)
            simctl = binary / 'xcrun'
            simctl.write_text('#!/bin/sh\nprintf "Unexpected simulator operation\\n" >&2\nexit 99\n')
            simctl.chmod(0o700)
            output = root / 'output'
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True,
                                    timeout=5, env=dict(os.environ, PATH=str(binary)+os.pathsep+os.environ['PATH'],
                                    SCOTTY_UNATTENDED='true', XCODE_PROJECT='Fixture.xcodeproj',
                                    XCODE_SCHEME='Fixture', XCODE_DESTINATION='', XCODE_CONFIGURATION='Debug',
                                    GITHUB_OUTPUT=str(output)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('Unexpected simulator operation', result.stderr)
            self.assertIn('destination=platform=iOS Simulator\n', output.read_text())
            self.assertIn('simulator-id=\n', output.read_text())


if __name__ == '__main__':
    unittest.main()

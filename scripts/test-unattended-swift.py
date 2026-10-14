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
                                    RUNNER_VERIFIER='true', XCODE_PROJECT='Fixture.xcodeproj',
                                    XCODE_SCHEME='Fixture', XCODE_DESTINATION='', XCODE_CONFIGURATION='Debug',
                                    GITHUB_OUTPUT=str(output)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('Unexpected simulator operation', result.stderr)
            self.assertIn('destination=platform=iOS Simulator\n', output.read_text())
            self.assertIn('simulator-id=\n', output.read_text())

    def direct_resolution(self, *, heading="Destinations compatible with the scheme:", compatible=True, newest_available=True):
        workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/ci_swift.yaml')], text=True))
        resolve = next(step for step in workflow['jobs']['build-and-test']['steps'] if step.get('id') == 'xcode')
        script = re.sub(r'\$\{\{ github\.(run_id|run_attempt|job) \}\}', 'fixture', resolve['run'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Fixture.xcodeproj').mkdir()
            binary = root / 'bin'
            binary.mkdir()
            xcode = binary / 'xcodebuild'
            xcode.write_text('#!/bin/sh\nif [ "$5" = "-showdestinations" ] || [ "$7" = "-showdestinations" ]; then\ncat <<EOF\n' + heading + '\n' + ('{ platform:iOS Simulator, id:new }\n{ platform:iOS Simulator, id:old }\n' if compatible else '') + 'Unavailable destinations for the scheme:\n{ platform:iOS Simulator, id:incompatible }\nEOF\nelse\nprintf "    SUPPORTED_PLATFORMS = iphonesimulator iphoneos\\n"\nfi\n')
            xcode.chmod(0o700)
            runtimes = ['com.apple.CoreSimulator.SimRuntime.iOS-' + version for version in ('9-3', '27-0', '28-0')]
            device_type = 'com.apple.CoreSimulator.SimDeviceType.iPhone-Example'
            inventory = {r: [dict(udid=identifier, isAvailable=True, deviceTypeIdentifier=device_type)] for r, identifier in zip(runtimes, ('old', 'new', 'incompatible'))}
            runtime_inventory = [{"identifier": r, "isAvailable": newest_available or r != runtimes[1]} for r in runtimes]
            simctl = binary / 'xcrun'
            simctl.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nruntimes = ' + repr(runtimes) + '\nif sys.argv[2:4] == ["list", "runtimes"]:\n print(json.dumps({"runtimes": ' + repr(runtime_inventory) + '}))\nelif sys.argv[2:4] == ["list", "devices"]:\n print(json.dumps({"devices": ' + repr(inventory) + '}))\nelif sys.argv[2] == "create":\n Path(os.environ["CREATED"]).write_text(json.dumps(sys.argv[3:]))\n print("owned-device")\n')
            simctl.chmod(0o700)
            output = root / 'output'
            created = root / 'created'
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True,
                                    timeout=10, env=dict(os.environ, PATH=str(binary)+os.pathsep+os.environ['PATH'],
                                    RUNNER_VERIFIER='false', XCODE_PROJECT='Fixture.xcodeproj',
                                    XCODE_SCHEME='Fixture', XCODE_DESTINATION='', XCODE_CONFIGURATION='Debug',
                                    GITHUB_OUTPUT=str(output), CREATED=str(created)))
            return result, json.loads(created.read_text()) if created.exists() else None

    def test_direct_lane_selects_latest_compatible_runtime(self):
        for heading in ("Destinations compatible with the scheme:", "Available destinations for the scheme:"):
            with self.subTest(heading=heading):
                result, created = self.direct_resolution(heading=heading)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(created[-1], "com.apple.CoreSimulator.SimRuntime.iOS-27-0")

    def test_direct_lane_excludes_unavailable_runtime(self):
        result, created = self.direct_resolution(newest_available=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(created[-1], "com.apple.CoreSimulator.SimRuntime.iOS-9-3")

    def test_direct_lane_no_compatible_destination_fails_before_creation(self):
        result, created = self.direct_resolution(compatible=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No available project-compatible", result.stderr)
        self.assertIsNone(created)

    def test_direct_test_step_disables_parallel_simulators(self):
        workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/ci_swift.yaml')], text=True))
        step = next(step for step in workflow['jobs']['build-and-test']['steps'] if step.get('name') == 'Test')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'xcodebuild'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            binary.chmod(0o700)
            result = subprocess.run(['bash', '-c', step['run']], capture_output=True, text=True, timeout=5,
                env=dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], XCODE_PROJECT='Fixture.xcodeproj',
                         XCODE_SCHEME='Fixture', XCODE_DESTINATION='id=owned', XCODE_CONFIGURATION='Debug', XCODE_DERIVED_DATA='.build'))
            self.assertEqual(result.returncode, 0, result.stderr)
            args = result.stdout.splitlines()
            self.assertEqual(args[args.index('-parallel-testing-enabled') + 1], 'NO')
            self.assertEqual(args[args.index('-maximum-concurrent-test-simulator-destinations') + 1], '1')

    def run_verifier(self, *, installed=True, tests='true', exit_code=0, inside_checkout=False):
        workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/ci_swift.yaml')], text=True))
        lane = next(s for s in workflow['jobs']['build-and-test']['steps'] if s.get('name') == 'Run runner-provisioned Xcode verification')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'checkout'
            workspace.mkdir()
            executable = (workspace if inside_checkout else root) / 'verifier with spaces'
            output = root / 'args'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FIXTURE_ARGS"\nexit '+str(exit_code)+'\n')
            executable.chmod(0o700)
            scheme = 'Fixture; touch injected'
            result = subprocess.run(['bash', '-c', lane['run']], cwd=workspace, capture_output=True, text=True, timeout=5,
                env=dict(os.environ, XCODE_VERIFIER=str(executable if installed else root / 'missing'),
                         XCODE_PROJECT='Fixture.xcodeproj', XCODE_SCHEME=scheme, XCODE_CONFIGURATION='Debug',
                         XCODE_PLATFORM='ios', RUN_TESTS=tests, FIXTURE_ARGS=str(output)))
            self.assertFalse((workspace / 'injected').exists())
            return result, output.read_text().splitlines() if output.exists() else []

    def test_fixed_arguments_preserve_scheme_without_shell_execution(self):
        result, args = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['--project','Fixture.xcodeproj','--scheme','Fixture; touch injected',
                                '--platform','ios','--configuration','Debug'])

    def test_build_only_flag(self):
        result, args = self.run_verifier(tests='false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[-1], '--no-tests')

    def test_missing_verifier_fails_without_fallback(self):
        result, args = self.run_verifier(installed=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires a provisioned absolute executable path', result.stderr)
        self.assertEqual(args, [])

    def test_checkout_executable_is_rejected(self):
        result, args = self.run_verifier(inside_checkout=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the checkout', result.stderr)
        self.assertEqual(args, [])

    def test_verifier_failure_propagates(self):
        result, _ = self.run_verifier(exit_code=23)
        self.assertEqual(result.returncode, 23)


if __name__ == '__main__':
    unittest.main()

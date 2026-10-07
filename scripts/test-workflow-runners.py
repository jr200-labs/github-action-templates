#!/usr/bin/env python3
"""Regression cases for ways workflows bypass runner selection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("lint", Path(__file__).with_name("lint-workflow-runners.py"))
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)
PROFILE = "${{ fromJSON(vars.RUNNER_PROFILES)[vars.RUNNER_PROFILE].default }}"


class RunnerPolicyTests(unittest.TestCase):
    def check(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci.yaml"
            path.write_text(source)
            return lint.errors_for(path)

    def test_profile_and_folded_profile(self):
        for value in [PROFILE, ">-\n      " + PROFILE]:
            with self.subTest(value=value):
                self.assertEqual([], self.check("on: pull_request\njobs:\n  ci:\n    runs-on: " + value))

    def test_literal_array_fixed_profile_and_fallback_bypasses(self):
        for value in ["ubuntu-latest", "macos-26", "arc-example", "[self-hosted, macOS]", ">-\n      macos-26", "${{ fromJSON(vars.RUNNER_PROFILES)['self-hosted'].default }}", "${{ inputs.runner || " + PROFILE[4:-3] + " }}", "${{ " + PROFILE[4:-3] + " || 'ubuntu-latest' }}", "${{ needs.setup.outputs.runner }}"]:
            with self.subTest(value=value):
                self.assertTrue(self.check("on: pull_request\njobs:\n  ci:\n    runs-on: " + value))

    def test_reusable_forwarding(self):
        for value in ["ubuntu-latest", "${{ inputs.runner }}", "${{ vars.RUNNER }}"]:
            with self.subTest(value=value):
                self.assertTrue(self.check("on: pull_request\njobs:\n  ci:\n    uses: owner/repo/.github/workflows/ci.yaml@master\n    with:\n      runner: " + value))
        self.assertEqual([], self.check("on: pull_request\njobs:\n  ci:\n    uses: owner/repo/.github/workflows/ci.yaml@master\n    with:\n      runner: " + PROFILE))

    def test_remote_call_without_runner_is_rejected(self):
        self.assertTrue(self.check("on: pull_request\njobs:\n  ci:\n    uses: owner/repo/.github/workflows/ci.yaml@master"))

    def test_reusable_input_requires_empty_default_and_no_dispatch(self):
        reusable = "on:\n  workflow_call:\n    inputs:\n      runner:\n        type: string\n        default: DEFAULT\njobs:\n  ci:\n    runs-on: ${{ inputs.runner }}"
        self.assertEqual([], self.check(reusable.replace("DEFAULT", "''")))
        self.assertTrue(self.check(reusable.replace("DEFAULT", "ubuntu-latest")))
        self.assertTrue(self.check(reusable.replace("workflow_call", "workflow_dispatch").replace("DEFAULT", "''")))

    def test_matrix_include_override(self):
        source = "on: pull_request\njobs:\n  ci:\n    runs-on: ${{ matrix.os }}\n    strategy:\n      matrix:\n        os:\n          - " + PROFILE
        self.assertEqual([], self.check(source))
        self.assertTrue(self.check(source + "\n        include:\n          - os: macos-26"))
        self.assertTrue(self.check(source.replace(PROFILE, "ubuntu-latest")))

    def test_duplicate_keys_fail_closed(self):
        self.assertTrue(self.check("on: pull_request\njobs:\n  ci:\n    runs-on: ubuntu-latest\n    runs-on: " + PROFILE))

    def test_hygiene_and_ruleset_install_required_guard(self):
        root = Path(__file__).resolve().parents[1]
        import yaml
        group = yaml.load((root / "consumers/groups/hygiene.yaml").read_text(), Loader=lint.Loader)
        self.assertIn("lint-workflow-runners", group["includes"])
        caller = yaml.load((root / "consumers/workflows/lint-workflow-runners.yaml").read_text(), Loader=lint.Loader)
        self.assertIn("pull_request", caller["on"])
        self.assertNotIn("paths", caller["on"].get("pull_request") or {})
        rules = json.loads((root / "rulesets/trunk-protect.json").read_text())["rules"]
        check_rule = next(rule for rule in rules if rule["type"] == "required_status_checks")
        self.assertIn({"context": "lint-workflow-runners / lint-workflow-runners"}, check_rule["parameters"]["required_status_checks"])

    def test_effective_variable_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps({"profile": "self-hosted", "roles": {"default": "arc-example", "macos": "example-macos"}}))
            profiles = json.dumps({"self-hosted": {"default": "arc-example", "macos": "example-macos"}})
            self.assertEqual([], lint.policy_errors(path, "self-hosted", profiles))
            self.assertTrue(lint.policy_errors(path, "hosted", profiles))
            self.assertTrue(lint.policy_errors(path, "self-hosted", profiles.replace("arc-example", "ubuntu-latest")))
            self.assertTrue(lint.policy_errors(path, "self-hosted", ""))


if __name__ == "__main__":
    unittest.main()

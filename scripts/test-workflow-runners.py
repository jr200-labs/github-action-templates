#!/usr/bin/env python3
"""Regression cases for ways workflows bypass runner selection."""
import importlib.util
import json
import os
import subprocess
import shutil
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


class LinterRuntimeIsolationTests(unittest.TestCase):
    """Shared guards must run independently of the caller's application Python."""

    def run_guard(self, guard, valid):
        root = Path(__file__).resolve().parents[1]
        import yaml
        workflow = yaml.load((root / ".github/workflows/lint_workflow_runners.yaml").read_text(), Loader=lint.Loader)
        retention_step = next(step["run"] for step in workflow["jobs"]["lint-workflow-runners"]["steps"] if "lint-artifact-retention.py" in step.get("run", ""))
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory)
            (caller / ".python-version").write_text("99.0.0\n")
            (caller / "pyproject.toml").write_text('[project]\nname = "example-caller"\nversion = "0.0.0"\nrequires-python = ">=99"\ndependencies = ["example-unavailable-dependency"]\n')
            shared_scripts = caller / "_gha_templates/scripts"
            shared_scripts.mkdir(parents=True)
            for filename in ("lint-workflow-runners.sh", "lint-workflow-runners.py", "lint-artifact-retention.py"):
                shutil.copy2(root / "scripts" / filename, shared_scripts / filename)
            workflows = caller / ".github/workflows"
            workflows.mkdir(parents=True)
            runner = PROFILE if valid else "ubuntu-latest"
            retention = 5 if valid else 90
            (workflows / "ci.yaml").write_text(f"on: push\njobs:\n  build:\n    runs-on: {runner}\n    steps:\n      - uses: actions/upload-artifact@v7\n        with:\n          retention-days: {retention}\n")
            environment = os.environ.copy()
            environment["UV_PYTHON_DOWNLOADS"] = "never"
            environment.pop("RUNNER_PROFILE", None)
            environment.pop("RUNNER_PROFILES", None)
            if guard == "runners":
                command = [str(shared_scripts / "lint-workflow-runners.sh"), str(workflows)]
            else:
                command = ["bash", "-c", retention_step]
            return subprocess.run(command, cwd=caller, env=environment, capture_output=True, text=True, timeout=30)

    def test_runner_guard_ignores_caller_python_and_still_enforces_policy(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                result = self.run_guard("runners", valid)
                self.assertEqual(0 if valid else 1, result.returncode, result.stdout + result.stderr)
                if not valid:
                    self.assertIn("literal", result.stdout + result.stderr)

    def test_retention_guard_ignores_caller_python_and_still_enforces_policy(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                result = self.run_guard("retention", valid)
                self.assertEqual(0 if valid else 1, result.returncode, result.stdout + result.stderr)
                if not valid:
                    self.assertIn("retention", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

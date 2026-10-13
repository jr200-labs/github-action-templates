#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Run the actual workflow scan step against deterministic command failures."""
import yaml
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [ROOT / ".github/workflows/drift-check-rulesets.yaml", ROOT / "consumers/workflows/drift-check-rulesets.yaml"]

class DriftTests(unittest.TestCase):
    def steps(self, workflow):
        return yaml.safe_load(workflow.read_text())["jobs"]["drift-check"]["steps"]

    def run_scan(self, workflow, response, status):
        step = next(s for s in self.steps(workflow) if s.get("id") == "dry")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            for relative in ["scripts/apply-rulesets.sh", ".gha-templates/scripts/apply-rulesets.sh"]:
                script = cwd / relative
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("#!/bin/bash\nprintf '%s\\n' '" + response + "'\nexit " + str(status) + "\n")
                script.chmod(0o755)
            (cwd / "targets.yaml").touch()
            env = {**os.environ, "GITHUB_WORKSPACE": str(cwd), "GITHUB_REPOSITORY_OWNER": "example-org", "RULESET_TARGETS_FILE": "targets.yaml", "RULESET_AUTOMERGE_CONFIG_FILE": "missing.json", "GITHUB_OUTPUT": str(cwd / "output")}
            result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd, env=env, capture_output=True, text=True)
            output = cwd / "output"
            return result, output.read_text() if output.exists() else ""

    def test_failure_reports_actual_error_and_never_reports_no_drift(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                result, output = self.run_scan(workflow, "permission denied from ruleset endpoint", 42)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("permission denied", result.stdout + result.stderr)
                self.assertNotIn("drift<<", output)

    def test_success_records_drift(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                result, output = self.run_scan(workflow, "DRY: POST missing author policy", 0)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("DRY: POST missing author policy", output)

    def test_failed_scan_can_notify_and_cannot_close_existing_drift(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                steps = self.steps(workflow)
                scan = next(s for s in steps if s.get("id") == "dry")
                self.assertTrue(scan.get("continue-on-error"))
                close = next(s for s in steps if s["name"].startswith("Close tracking"))
                self.assertIn("steps.dry.outcome == 'success'", close["if"])
                notify = next(s for s in steps if s["name"] == "Report failed ruleset scan")
                self.assertIn("always()", notify["if"])
                self.assertIn("steps.dry.outcome != 'success'", notify["if"])
                self.assertEqual("Fail incomplete ruleset scan", steps[-1]["name"])
                self.assertIn("exit 1", steps[-1]["run"])

if __name__ == "__main__":
    unittest.main()

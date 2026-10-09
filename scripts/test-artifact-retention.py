#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Exercise retention enforcement through the real workflow scanner."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("retention", Path(__file__).with_name("lint-artifact-retention.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

class RetentionTests(unittest.TestCase):
    def check(self, steps, env=""):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "ci.yaml"
            file.write_text("name: ci\non: push\n" + env + "jobs:\n  build:\n    runs-on: example\n    steps:\n" + steps)
            return policy.scan(file)

    def test_old_app_bundle_policy_is_rejected(self):
        self.assertTrue(self.check("      - uses: actions/upload-artifact@v7\n        with:\n          retention-days: 14\n"))

    def test_implicit_and_dynamic_retention_are_rejected(self):
        for value in ["", "          retention-days: 0\n", "          retention-days: '${{ inputs.days }}'\n", "          retention-days: 365\n"]:
            self.assertTrue(self.check("      - uses: actions/upload-artifact@v7\n        with:\n          name: report\n" + value))

    def test_shorter_and_five_day_retention_are_allowed(self):
        for days in [1, 5]:
            self.assertEqual([], self.check(f"      - uses: actions/upload-artifact@v7\n        with:\n          retention-days: {days}\n"))

    def test_buildx_inherits_policy_but_cannot_override_it(self):
        step = "      - uses: docker/build-push-action@v7\n"
        self.assertTrue(self.check(step))
        self.assertEqual([], self.check(step, "env:\n  DOCKER_BUILD_RECORD_RETENTION_DAYS: 5\n"))
        self.assertTrue(self.check(step + "        env:\n          DOCKER_BUILD_RECORD_RETENTION_DAYS: 90\n", "env:\n  DOCKER_BUILD_RECORD_RETENTION_DAYS: 5\n"))
        self.assertEqual([], self.check(step, "env:\n  DOCKER_BUILD_RECORD_UPLOAD: false\n"))

    def test_pages_default_is_one_day_and_override_is_checked(self):
        self.assertEqual([], self.check("      - uses: actions/upload-pages-artifact@v5\n"))
        self.assertTrue(self.check("      - uses: actions/upload-pages-artifact@v5\n        with:\n          retention-days: 90\n"))

    def test_local_composite_uploads_are_checked(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "action.yml"
            file.write_text("runs:\n  using: composite\n  steps:\n    - uses: actions/upload-artifact@v7\n")
            self.assertTrue(policy.scan(file))

if __name__ == "__main__":
    unittest.main()

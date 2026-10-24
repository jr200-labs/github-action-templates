#!/usr/bin/env python3
"""Execute the real targeted workflow step without contacting GitHub."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def targeted_step():
    text = (ROOT / ".github/workflows/renovate.yaml").read_text()
    step = text.split("      - name: Build targeted Renovate config\n", 1)[1]
    run = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return "\n".join(line.removeprefix("          ") for line in run.splitlines())


def matches(patterns, name):
    positive = [pattern for pattern in patterns if not pattern.startswith("!")]

    def match(pattern):
        if pattern.startswith("/") and pattern.endswith("/"):
            return re.search(pattern[1:-1], name) is not None
        return pattern.lower() == name.lower()

    return (not positive or any(match(pattern) for pattern in positive)) and not any(
        match(pattern[1:]) for pattern in patterns if pattern.startswith("!")
    )


class TargetedConfigTest(unittest.TestCase):
    def generate(self, dependencies):
        base = {
            "extends": ["github>example/preset"],
            "packageRules": [
                {"matchManagers": ["helm-values"], "enabled": False},
                {"matchDepNames": ["blocked"], "enabled": False},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".renovate-out").mkdir()
            (root / "base.json").write_text(json.dumps(base))
            environment = dict(
                os.environ,
                DEPENDENCY_NAMES=json.dumps(dependencies),
                BASE_CONFIG="base.json",
                GITHUB_WORKSPACE=directory,
                GITHUB_OUTPUT=str(root / "output"),
            )
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", targeted_step()],
                check=False,
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = json.loads(
                (root / ".renovate-out/targeted-config.json").read_text()
            )
            self.assertIn(
                "config-file=.renovate-out/targeted-config.json",
                (root / "output").read_text(),
            )
        self.assertEqual(generated["extends"], base["extends"])
        self.assertEqual(generated["packageRules"][:2], base["packageRules"])
        self.assertFalse(generated["pruneStaleBranches"])
        return generated["packageRules"][2:]

    def test_targeting_does_not_reenable_a_disabled_scanner(self):
        filters = self.generate(["ghcr.io/example/widget", "blocked"])
        for manager, originally_enabled, name, expected in [
            ("helm-values", False, "ghcr.io/example/widget", False),
            ("custom.regex", True, "ghcr.io/example/widget", True),
            ("npm", False, "blocked", False),
            ("custom.regex", True, "ghcr.io/example/other", False),
        ]:
            enabled = originally_enabled
            for rule in filters:
                if matches(
                    rule.get("matchDepNames", rule.get("matchPackageNames", [])), name
                ):
                    enabled = rule["enabled"]
            self.assertEqual(enabled, expected, (manager, name, filters))

    def test_names_are_literal_and_lookup_package_identity_is_not_used(self):
        names = [
            "@scope/pkg",
            "example/pkg.name+suffix",
            "!special",
            "name[abc]",
            "back\\slash",
        ]
        filters = self.generate(names + names)
        self.assertEqual(len(filters), 1)
        rule = filters[0]
        self.assertFalse(rule["enabled"])
        self.assertEqual(len(rule["matchDepNames"]), len(names))
        for name in names:
            self.assertFalse(matches(rule["matchDepNames"], name), name)
        for name in [
            "@scope/pkg-extra",
            "example/pkgXname+suffix",
            "namea",
            "unselected",
        ]:
            self.assertTrue(matches(rule["matchDepNames"], name), name)
        self.assertNotIn("matchPackageNames", rule)


if __name__ == "__main__":
    unittest.main()

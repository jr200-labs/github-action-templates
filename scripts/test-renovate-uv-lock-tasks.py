#!/usr/bin/env python3
"""Exercise the preset's post-upgrade commands against real uv projects."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "default.json").read_text())
TASKS = [
    rule["postUpgradeTasks"]
    for rule in CONFIG["packageRules"]
    if rule.get("postUpgradeTasks", {}).get("commands") == ["uv lock --refresh"]
]


class UvLockTasksTest(unittest.TestCase):
    def setUp(self):
        self.assertEqual(len(TASKS), 2, "Expected both Python post-upgrade rules")

    def test_root_and_grouped_nested_projects(self):
        # Branch tasks have no individual packageFileDir. Update tasks keep
        # that context even when several projects share a dependency PR.
        for task in TASKS:
            with self.subTest(task=task):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    projects = (root, root / "services/api", root / "tools/worker")
                    for index, project in enumerate(projects):
                        project.mkdir(parents=True, exist_ok=True)
                        (project / "pyproject.toml").write_text(
                            '[project]\n'
                            f'name = "fixture-{index}"\n'
                            'version = "0.1.0"\n'
                            'requires-python = ">=3.9"\n'
                            'dependencies = []\n'
                        )
                    environment = dict(
                        os.environ,
                        UV_PYTHON_DOWNLOADS="never",
                        UV_CACHE_DIR=str(root / "cache"),
                    )
                    # Execute the configured command with the same package
                    # context used by Renovate's update task executor.
                    for project in projects:
                        relative = project.relative_to(root).as_posix()
                        context = relative if task["executionMode"] == "update" else ""
                        template = task.get("workingDirTemplate", "")
                        working_dir = template.replace("{{{packageFileDir}}}", context)
                        result = subprocess.run(
                            task["commands"][0].split(),
                            cwd=root / working_dir,
                            env=environment,
                            text=True,
                            capture_output=True,
                            timeout=30,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        lock = project / "uv.lock"
                        self.assertTrue(lock.is_file(), f"Missing {relative}/uv.lock")

    def test_nested_project_without_root_manifest(self):
        for task in TASKS:
            with self.subTest(task=task):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    project = root / "services/api"
                    project.mkdir(parents=True)
                    (project / "pyproject.toml").write_text(
                        '[project]\nname = "nested-fixture"\nversion = "0.1.0"\n'
                        'requires-python = ">=3.9"\ndependencies = []\n'
                    )
                    context = "services/api" if task["executionMode"] == "update" else ""
                    working_dir = task.get("workingDirTemplate", "").replace(
                        "{{{packageFileDir}}}", context
                    )
                    result = subprocess.run(
                        task["commands"][0].split(),
                        cwd=root / working_dir,
                        env=dict(
                            os.environ,
                            UV_PYTHON_DOWNLOADS="never",
                            UV_CACHE_DIR=str(root / "cache"),
                        ),
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue((project / "uv.lock").is_file())


if __name__ == "__main__":
    unittest.main()

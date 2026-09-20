"""Offline contract tests for configurable app packaging and canonical adoption."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "consumers/files/.shared/package-macos-app.py"
spec = importlib.util.spec_from_file_location("macos_package", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackagingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = Path.cwd()
        os.chdir(temporary.name)
        self.addCleanup(os.chdir, previous)
        self.config = dict(app="dist/Example.app", artifact="example-macos", build=["build-fixture"])
        self.output = Path("assets")

    def run_command(self, command, **kwargs):
        if command == ["build-fixture"]:
            contents = Path(self.config["app"]) / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps(dict(CFBundleShortVersionString="1.2.3")))
        elif command[0] == "/usr/bin/ditto":
            Path(command[-1]).write_bytes(b"fixture archive")
        return subprocess.CompletedProcess(command, 0)

    def test_archive_checksum_version_and_signing(self):
        with patch.object(module.subprocess, "run", side_effect=self.run_command) as run:
            module.package(self.config, self.output, "v1.2.3")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][:4], ["/usr/bin/codesign", "--verify", "--deep", "--strict"])
        self.assertIn("--keepParent", commands[2])
        self.assertEqual((self.output / "example-macos.zip.sha256").read_text(),
                         hashlib.sha256(b"fixture archive").hexdigest() + "  example-macos.zip\n")
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            module.package(self.config, self.output)

    def test_failed_build_signature_or_version_cannot_produce_assets(self):
        for failure in ("build", "signature", "version"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=Path.cwd()) as work:
                config = dict(self.config, app=str(Path(work).relative_to(Path.cwd()) / "Example.app"))
                original = self.config
                self.config = config
                def run(command, **kwargs):
                    if (failure == "build" and command == ["build-fixture"]) or (failure == "signature" and command[0] == "/usr/bin/codesign"):
                        raise subprocess.CalledProcessError(1, command)
                    return self.run_command(command, **kwargs)
                with patch.object(module.subprocess, "run", side_effect=run):
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        module.package(config, self.output, "v9.9.9" if failure == "version" else "v1.2.3")
                self.assertFalse(self.output.exists())
                self.config = original

    def test_config_rejects_unsafe_paths_and_output_injection(self):
        for change in ({"app": "../outside.app"}, {"app": "/outside.app"}, {"artifact": "x\nsetup_uv=true"},
                       {"artifact": "../x"}, {"build": "sh -c injected"}, {"setup_uv": "false"}, {"unknown": 1}):
            Path("config.json").write_text(json.dumps(dict(self.config, **change)))
            with self.assertRaises(ValueError):
                module.configuration("config.json")
        Path("config.json").write_text(json.dumps(self.config))
        self.assertEqual(module.configuration("config.json"), self.config)

    def test_group_sync_and_drift(self):
        Path(".github").mkdir()
        Path(".github/.shared-config.yaml").write_text("ref: shared-v0.1.0\nworkflows:\n  - macos-app\n")
        env = dict(os.environ, STRICT="1", SYNC_BASE_URL=(ROOT / "consumers").as_uri())
        sync = ["bash", str(ROOT / "consumers/scripts/sync-shared")]
        subprocess.run(sync, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        caller = Path(".github/workflows/macos-app.yaml")
        self.assertEqual(caller.read_bytes(), (ROOT / "consumers/workflows/macos-app.yaml").read_bytes())
        self.assertEqual(Path(".shared/package-macos-app.py").read_bytes(), SCRIPT.read_bytes())
        subprocess.run(sync + ["--check"], check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        Path(".shared/package-macos-app.py").write_text("drift")
        self.assertNotEqual(subprocess.run(sync + ["--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode, 0)

    def test_publish_checks_checksum_before_upload_and_never_clobbers(self):
        with patch.object(module.subprocess, "run", side_effect=self.run_command):
            module.package(self.config, Path("macos-release"), "v1.2.3")
        Path("bin").mkdir()
        gh = Path("bin/gh")
        gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RUNNER_TEMP/upload-args"\n')
        gh.chmod(0o755)
        step = subprocess.check_output(["yq", "-r", ".jobs.publish.steps[-1].run",
                                        str(ROOT / ".github/workflows/build_macos_app.yaml")], text=True)
        env = dict(os.environ, RUNNER_TEMP=str(Path.cwd()), ARTIFACT="example-macos", RELEASE_TAG="v1.2.3",
                   PATH=str(Path("bin").resolve()) + os.pathsep + os.environ["PATH"])
        subprocess.run(["bash", "-c", step], check=True, env=env, stdout=subprocess.PIPE)
        self.assertEqual(Path("upload-args").read_text().splitlines(),
                         ["release", "upload", "v1.2.3", "example-macos.zip", "example-macos.zip.sha256"])
        Path("upload-args").unlink()
        Path("macos-release/example-macos.zip").write_bytes(b"corrupt")
        result = subprocess.run(["bash", "-c", step], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path("upload-args").exists())


if __name__ == "__main__":
    unittest.main()

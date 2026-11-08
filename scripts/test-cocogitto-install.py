#!/usr/bin/env python3
"""Exercise the installer with successful and failed release downloads offline."""

import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "actions/setup-cocogitto/install.sh"


class CocogittoInstallTests(unittest.TestCase):
    def run_install(self, mode):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        tools = root / "tools"
        tools.mkdir()
        (tools / "curl").write_text('''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
assert "--fail" in args and "--retry" in args and "--max-time" in args
assert args[-1] == "https://github.com/cocogitto/cocogitto/releases/download/7.0.0/cocogitto-7.0.0-x86_64-unknown-linux-musl.tar.gz"
if os.environ["DOWNLOAD_MODE"] == "http_error":
    sys.exit(22)
shutil.copyfile(os.environ["FIXTURE_ARCHIVE"], args[args.index("--output") + 1])
''')
        (tools / "curl").chmod(0o755)
        archive = root / "fixture.tar.gz"
        if mode == "invalid_archive":
            archive.write_text("upstream gateway error")
        else:
            binary = b"#!/bin/sh\nprintf 'cog 7.0.0\\n'\n"
            if mode == "broken_binary":
                binary = b"#!/bin/sh\nexit 1\n"
            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo("x86_64-unknown-linux-musl/cog")
                entry.mode = 0o755
                entry.size = len(binary)
                output.addfile(entry, io.BytesIO(binary))
        path_file = root / "github_path"
        path_file.touch()
        env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                   RUNNER_TEMP=str(root), RUNNER_OS="Linux", RUNNER_ARCH="X64",
                   GITHUB_PATH=str(path_file), COCOGITTO_VERSION="7.0.0",
                   DOWNLOAD_MODE=mode, FIXTURE_ARCHIVE=str(archive))
        result = subprocess.run(["bash", str(INSTALLER)], env=env, text=True,
                                capture_output=True, timeout=10)
        return result, root, path_file

    def test_success_publishes_working_binary(self):
        result, root, path_file = self.run_install("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = root / "cocogitto/bin/cog"
        self.assertTrue(installed.is_file())
        self.assertEqual(path_file.read_text().strip(), str(installed.parent))
        self.assertEqual(subprocess.check_output([str(installed), "--version"], text=True).strip(), "cog 7.0.0")

    def test_failed_download_or_binary_never_publishes_installation(self):
        for mode in ("http_error", "invalid_archive", "broken_binary"):
            with self.subTest(mode=mode):
                result, root, path_file = self.run_install(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(path_file.read_text(), "")
                self.assertFalse((root / "cocogitto/bin/cog").exists())


if __name__ == "__main__":
    unittest.main()

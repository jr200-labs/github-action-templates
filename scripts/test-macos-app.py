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

    def test_sparkle_appcast_uses_verified_archive_and_bounded_generator(self):
        self.config["sparkle"] = dict(appcast="appcast.xml", generate=["sign-fixture"])
        with patch.object(module.subprocess, "run", side_effect=self.run_command):
            module.package(self.config, self.output, "v1.2.3")
        observed = []
        def generate(command, **kwargs):
            observed.append((command, kwargs))
            Path(command[command.index("--output") + 1]).write_text("<rss/>")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(module.subprocess, "run", side_effect=generate):
            module.appcast(self.config, self.output, "v1.2.3", "example/app")
        command, kwargs = observed[0]
        self.assertEqual(command[0], "sign-fixture")
        self.assertEqual(Path(command[command.index("--archive") + 1]).name, "example-macos.zip")
        self.assertEqual(command[command.index("--download-url-prefix") + 1],
                         "https://github.com/example/app/releases/download/v1.2.3/")
        self.assertEqual(kwargs["timeout"], 300)
        self.assertEqual((self.output / "appcast.xml").read_text(), "<rss/>")
        with self.assertRaisesRegex(ValueError, "new output"):
            module.appcast(self.config, self.output, "v1.2.3", "example/app")

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
                       {"artifact": "../x"}, {"build": "sh -c injected"}, {"setup_uv": "false"},
                       {"sparkle": {"appcast": "../feed.xml", "generate": ["sign"]}},
                       {"sparkle": {"appcast": "feed.txt", "generate": ["sign"]}},
                       {"sparkle": {"appcast": "feed.xml", "generate": "sh -c injected"}}, {"unknown": 1}):
            Path("config.json").write_text(json.dumps(dict(self.config, **change)))
            with self.assertRaises(ValueError):
                module.configuration("config.json")
        Path("config.json").write_text(json.dumps(self.config))
        self.assertEqual(module.configuration("config.json"), self.config)

    def test_group_sync_and_drift(self):
        Path(".github").mkdir()
        Path(".github/.shared-config.yaml").write_text("ref: shared-v0.1.0\nworkflows:\n  - macos-app\n")
        Path("release-please-config.json").write_bytes((ROOT / "shared/release-please-config.base.json").read_bytes())
        env = dict(os.environ, STRICT="1", SYNC_BASE_URL=(ROOT / "consumers").as_uri())
        sync = ["bash", str(ROOT / "consumers/scripts/sync-shared")]
        subprocess.run(sync, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        caller = Path(".github/workflows/macos-app.yaml")
        publisher = Path(".github/workflows/publish-macos-app.yaml")
        self.assertEqual(caller.read_bytes(), (ROOT / "consumers/workflows/macos-app.yaml").read_bytes())
        self.assertEqual(publisher.read_bytes(), (ROOT / "consumers/workflows/publish-macos-app.yaml").read_bytes())
        self.assertEqual(Path(".shared/package-macos-app.py").read_bytes(), SCRIPT.read_bytes())
        caller_data = subprocess.check_output(["yq", "-o=json", ".", str(caller)], text=True)
        self.assertNotIn("repository_dispatch", caller_data)
        self.assertNotIn("SPARKLE_EDDSA_PRIVATE_KEY", caller_data)
        publisher_secret = subprocess.check_output(
            ["yq", "-r", ".jobs.app.secrets.SPARKLE_EDDSA_PRIVATE_KEY", str(publisher)], text=True
        ).strip()
        self.assertEqual(publisher_secret, "${{ secrets.SPARKLE_EDDSA_PRIVATE_KEY }}")
        release_config = json.loads(Path("release-please-config.json").read_text())
        self.assertIs(release_config["draft"], True)
        self.assertIs(release_config["force-tag-creation"], True)
        subprocess.run(sync + ["--check"], check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        release_config.pop("draft")
        Path("release-please-config.json").write_text(json.dumps(release_config))
        drift = subprocess.run(sync + ["--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("must set draft and force-tag-creation", drift.stdout)
        subprocess.run(sync, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        Path(".shared/package-macos-app.py").write_text("drift")
        self.assertNotEqual(subprocess.run(sync + ["--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode, 0)

    def test_macos_release_config_requires_atomic_draft_publication(self):
        Path(".github").mkdir()
        Path(".github/macos-app.json").write_text("{}\n")
        config = json.loads((ROOT / "shared/release-please-config.base.json").read_text())
        config["release-type"] = "simple"
        Path("release-please-config.json").write_text(json.dumps(config))
        lint = ["bash", str(ROOT / "shared/lint-release-please-config.sh")]
        rejected = subprocess.run(lint, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must be created as drafts", rejected.stdout)
        config.update(draft=True, **{"force-tag-creation": True})
        Path("release-please-config.json").write_text(json.dumps(config))
        subprocess.run(lint, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_publish_checks_checksum_before_upload_and_never_clobbers(self):
        with patch.object(module.subprocess, "run", side_effect=self.run_command):
            module.package(self.config, Path("macos-release"), "v1.2.3")
        Path("bin").mkdir()
        gh = Path("bin/gh")
        gh.write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$RUNNER_TEMP/gh-commands"
if [ "$1" = "api" ]; then
  if [ "${2:-}" = "--method" ] && [ "${3:-}" = "PATCH" ]; then
    :
  else
    printf '{"id":123,"tag_name":"v1.2.3","draft":true,"upload_url":"https://uploads.github.com/repos/example/app/releases/123/assets{?name,label}","assets":['
    separator=""
    assets="${PREEXISTING_ASSETS:-}"
    if [ -f "$RUNNER_TEMP/uploaded-assets" ]; then assets="$assets $(cat "$RUNNER_TEMP/uploaded-assets")"; fi
    for asset in $assets; do
      if [ "$asset" = "${OMIT_ASSET:-}" ]; then continue; fi
      asset_path="$asset"
      if [ ! -f "$asset_path" ]; then asset_path="$RUNNER_TEMP/macos-release/$asset"; fi
      digest=$(shasum -a 256 "$asset_path" | awk '{print $1}')
      printf '%s{"name":"%s","state":"uploaded","digest":"sha256:%s"}' "$separator" "$asset" "$digest"
      separator=,
    done
    printf ']}\\n'
  fi
fi
''')
        gh.chmod(0o755)
        curl = Path("bin/curl")
        curl.write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$RUNNER_TEMP/curl-commands"
for argument in "$@"; do
  case "$argument" in
    https://uploads.github.com/*?name=*) printf '%s\\n' "${argument##*=}" >> "$RUNNER_TEMP/uploaded-assets" ;;
  esac
done
''')
        curl.chmod(0o755)
        step = subprocess.check_output(["yq", "-r", ".jobs.publish.steps[-1].run",
                                        str(ROOT / ".github/workflows/publish_macos_app.yaml")], text=True)
        env = dict(os.environ, RUNNER_TEMP=str(Path.cwd()), GITHUB_REPOSITORY="example/app",
                   ARTIFACT="example-macos", APPCAST="", RELEASE_TAG="v1.2.3", RELEASE_ID="123",
                   GH_TOKEN="test-token",
                   EXPECTED_ASSETS="example-macos.zip example-macos.zip.sha256",
                   PATH=str(Path("bin").resolve()) + os.pathsep + os.environ["PATH"])
        subprocess.run(["bash", "-c", step], check=True, env=env, stdout=subprocess.PIPE)
        self.assertEqual(Path("gh-commands").read_text().splitlines(), [
            "api repos/example/app/releases/123",
            "api repos/example/app/releases/123",
            "api repos/example/app/releases/123",
            "api repos/example/app/releases/123",
            "api --method PATCH repos/example/app/releases/123 -F draft=false -f make_latest=true",
        ])
        self.assertEqual(len(Path("curl-commands").read_text().splitlines()), 2)
        Path("macos-release/appcast.xml").write_text("<rss/>")
        Path("gh-commands").unlink()
        Path("curl-commands").unlink()
        Path("uploaded-assets").unlink()
        sparkle_env = dict(env, APPCAST="appcast.xml",
                           EXPECTED_ASSETS="example-macos.zip example-macos.zip.sha256 appcast.xml")
        subprocess.run(["bash", "-c", step], check=True, env=sparkle_env, stdout=subprocess.PIPE)
        self.assertTrue(any(command.endswith("assets?name=appcast.xml")
                            for command in Path("curl-commands").read_text().splitlines()))
        Path("gh-commands").unlink()
        Path("curl-commands").unlink()
        Path("uploaded-assets").unlink()
        retry_env = dict(env, PREEXISTING_ASSETS="example-macos.zip example-macos.zip.sha256")
        subprocess.run(["bash", "-c", step], check=True, env=retry_env, stdout=subprocess.PIPE)
        self.assertFalse(Path("curl-commands").exists())
        Path("gh-commands").unlink()
        incomplete_env = dict(env, OMIT_ASSET="example-macos.zip.sha256")
        incomplete = subprocess.run(["bash", "-c", step], env=incomplete_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(incomplete.returncode, 0)
        self.assertNotIn("--method PATCH", Path("gh-commands").read_text())
        Path("gh-commands").unlink()
        Path("uploaded-assets").unlink()
        Path("macos-release/example-macos.zip").write_bytes(b"corrupt")
        result = subprocess.run(["bash", "-c", step], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(Path("gh-commands").read_text().splitlines(),
                         ["api repos/example/app/releases/123"])

    def test_publish_resolves_legacy_tag_input_to_one_draft_release_id(self):
        Path("bin").mkdir()
        gh = Path("bin/gh")
        gh.write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$RUNNER_TEMP/gh-commands"
printf '[[{"id":123,"tag_name":"v1.2.3","draft":true,"upload_url":"https://uploads.github.com/repos/example/app/releases/123/assets{?name,label}"}]]\\n'
''')
        gh.chmod(0o755)
        step = subprocess.check_output(
            ["yq", "-r", ".jobs.resolve.steps[-1].run", str(ROOT / ".github/workflows/publish_macos_app.yaml")],
            text=True,
        )
        env = dict(os.environ, RUNNER_TEMP=str(Path.cwd()), GITHUB_REPOSITORY="example/app",
                   GITHUB_OUTPUT=str(Path("output").resolve()), RELEASE_TAG="v1.2.3", RELEASE_ID="",
                   PATH=str(Path("bin").resolve()) + os.pathsep + os.environ["PATH"])
        subprocess.run(["bash", "-c", step], check=True, env=env, stdout=subprocess.PIPE)
        self.assertEqual(Path("output").read_text(), "release-id=123\n")
        self.assertEqual(Path("gh-commands").read_text().strip(),
                         "api --paginate --slurp repos/example/app/releases?per_page=100")

    def test_signing_secret_is_limited_to_release_appcast_step(self):
        workflow = ROOT / ".github/workflows/build_macos_app.yaml"
        appcast_step = subprocess.check_output(
            ["yq", "-o=json", ".jobs.package.steps[] | select(.name == \"Generate signed Sparkle appcast\")", str(workflow)],
            text=True,
        )
        step = json.loads(appcast_step)
        self.assertEqual(step["if"], "inputs.generate-appcast && steps.config.outputs.appcast != ''")
        self.assertEqual(step["env"]["SPARKLE_EDDSA_PRIVATE_KEY"], "${{ secrets.SPARKLE_EDDSA_PRIVATE_KEY }}")
        build_step = subprocess.check_output(
            ["yq", "-o=json", ".jobs.package.steps[] | select(.name == \"Build, verify and archive\")", str(workflow)],
            text=True,
        )
        self.assertNotIn("SPARKLE_EDDSA_PRIVATE_KEY", build_step)

    def test_ci_and_release_workflows_are_separate(self):
        build = ROOT / ".github/workflows/build_macos_app.yaml"
        publish = ROOT / ".github/workflows/publish_macos_app.yaml"
        build_jobs = json.loads(subprocess.check_output(["yq", "-o=json", ".jobs", str(build)], text=True))
        publish_jobs = json.loads(subprocess.check_output(["yq", "-o=json", ".jobs", str(publish)], text=True))
        self.assertEqual(list(build_jobs), ["package"])
        self.assertEqual(list(publish_jobs), ["resolve", "package", "publish"])
        publish_text = publish.read_text()
        self.assertNotIn("/releases/tags/", publish_text)
        self.assertNotIn("gh release ", publish_text)


if __name__ == "__main__":
    unittest.main()

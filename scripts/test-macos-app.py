"""Offline contract tests for configurable app packaging and canonical adoption."""

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "consumers/files/.shared/package-macos-app.py"
spec = importlib.util.spec_from_file_location("macos_package", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
APPCAST_SCRIPT = ROOT / "consumers/files/.shared/generate-sparkle-appcast.py"
appcast_spec = importlib.util.spec_from_file_location("sparkle_appcast", APPCAST_SCRIPT)
appcast_module = importlib.util.module_from_spec(appcast_spec)
appcast_spec.loader.exec_module(appcast_module)
RELEASE_SCRIPT = ROOT / "actions/macos-release/release.py"
release_spec = importlib.util.spec_from_file_location("macos_release", RELEASE_SCRIPT)
release_module = importlib.util.module_from_spec(release_spec)
release_spec.loader.exec_module(release_module)


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

    def test_pr_build_verifies_without_creating_release_assets(self):
        with patch.object(module.subprocess, "run", side_effect=self.run_command) as run:
            module.package(self.config, self.output, create_release_assets=False)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][:4], ["/usr/bin/codesign", "--verify", "--deep", "--strict"])
        self.assertFalse(any(command[0] == "/usr/bin/ditto" for command in commands))
        self.assertFalse(self.output.exists())

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

    def test_default_sparkle_generator_rejects_malformed_private_keys(self):
        Path("archive.zip").write_bytes(b"archive")
        arguments = [
            "generate-sparkle-appcast.py",
            "--archive",
            "archive.zip",
            "--output",
            "appcast.xml",
            "--download-url-prefix",
            "https://github.com/example/app/releases/download/v1.2.3/",
        ]
        for private_key in ("not-base64", "YWJj"):
            with (
                self.subTest(private_key=private_key),
                patch.object(sys, "argv", arguments),
                patch.dict(os.environ, {"SPARKLE_EDDSA_PRIVATE_KEY": private_key}),
                self.assertRaises(SystemExit),
            ):
                appcast_module.main()

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
        Path(".github/.shared-config.yaml").write_text(
            "ref: shared-v0.1.0\nworkflows:\n  - macos-app\n  - sparkle-development\n"
        )
        Path("release-please-config.json").write_bytes((ROOT / "shared/release-please-config.base.json").read_bytes())
        env = dict(os.environ, STRICT="1", SYNC_BASE_URL=(ROOT / "consumers").as_uri())
        sync = ["bash", str(ROOT / "consumers/scripts/sync-shared")]
        subprocess.run(sync, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        caller = Path(".github/workflows/macos-app.yaml")
        publisher = Path(".github/workflows/publish-macos-app.yaml")
        self.assertEqual(caller.read_bytes(), (ROOT / "consumers/workflows/macos-app.yaml").read_bytes())
        self.assertEqual(publisher.read_bytes(), (ROOT / "consumers/workflows/publish-macos-app.yaml").read_bytes())
        self.assertEqual(Path(".shared/package-macos-app.py").read_bytes(), SCRIPT.read_bytes())
        self.assertEqual(
            Path(".sparkle-public-key").read_bytes(),
            (ROOT / "consumers/files/.sparkle-public-key").read_bytes(),
        )
        self.assertEqual(len(base64.b64decode(Path(".sparkle-public-key").read_text().strip(), validate=True)), 32)
        for helper in ("fetch-sparkle.py", "generate-sparkle-appcast.py", "sparkle.json"):
            self.assertEqual(
                Path(".shared", helper).read_bytes(),
                (ROOT / "consumers/files/.shared" / helper).read_bytes(),
            )
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
        Path(".sparkle-public-key").write_text("different\n")
        public_drift = subprocess.run(
            sync + ["--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(public_drift.returncode, 0)
        self.assertIn(".sparkle-public-key differs from canonical", public_drift.stdout)
        subprocess.run(sync, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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

    def release_api(self, release):
        calls = []

        def request(repository, token, path, method="GET", body=None, upload=False):
            calls.append((method, path, upload))
            if method == "POST":
                name = path.split("?name=", 1)[1]
                release["assets"].append({
                    "name": name, "state": "uploaded",
                    "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                })
                return release["assets"][-1]
            if method == "PATCH":
                self.assertEqual(body, {"draft": False, "make_latest": "true"})
                release["draft"] = False
                return release
            return dict(release, assets=[dict(asset) for asset in release["assets"]])

        return request, calls

    def test_publish_checks_checksum_before_upload_and_never_clobbers(self):
        with patch.object(module.subprocess, "run", side_effect=self.run_command):
            module.package(self.config, Path("macos-release"), "v1.2.3")
        release = {
            "id": 123, "tag_name": "v1.2.3", "draft": True,
            "upload_url": "https://uploads.github.com/repos/example/app/releases/123/assets{?name,label}",
            "assets": [],
        }
        api, calls = self.release_api(release)
        with patch.object(release_module, "api_request", side_effect=api):
            release_module.publish_release("example/app", "v1.2.3", 123, "test-token-which-is-long", Path("macos-release"), "example-macos")
        self.assertFalse(release["draft"])
        self.assertEqual(sorted(asset["name"] for asset in release["assets"]),
                         ["example-macos.zip", "example-macos.zip.sha256"])
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 2)
        self.assertEqual(calls[-1][0], "PATCH")

        release["draft"] = True
        calls.clear()
        with patch.object(release_module, "api_request", side_effect=api):
            release_module.publish_release("example/app", "v1.2.3", 123, "test-token-which-is-long", Path("macos-release"), "example-macos")
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 0)

        release["draft"] = True
        release["assets"].append({"name": "unverified.zip", "state": "uploaded", "digest": "sha256:bad"})
        with patch.object(release_module, "api_request", side_effect=api), self.assertRaisesRegex(ValueError, "outside"):
            release_module.publish_release("example/app", "v1.2.3", 123, "test-token-which-is-long", Path("macos-release"), "example-macos")

        release["assets"].pop()
        Path("macos-release/example-macos.zip").write_bytes(b"corrupt")
        with patch.object(release_module, "api_request", side_effect=api), self.assertRaisesRegex(ValueError, "checksum"):
            release_module.publish_release("example/app", "v1.2.3", 123, "test-token-which-is-long", Path("macos-release"), "example-macos")

    def test_publish_resolves_release_identity_without_host_cli(self):
        release = {
            "id": 123, "tag_name": "v1.2.3", "draft": True,
            "upload_url": "https://uploads.github.com/repos/example/app/releases/123/assets{?name,label}",
            "assets": [],
        }
        with patch.object(release_module, "api_request", return_value=[release]) as request:
            self.assertEqual(release_module.resolve_release("example/app", "v1.2.3", "", "test-token-which-is-long"), 123)
        request.assert_called_once_with("example/app", "test-token-which-is-long",
                                        "/repos/example/app/releases?per_page=100&page=1")
        with patch.object(release_module, "api_request", return_value=release) as request:
            self.assertEqual(release_module.resolve_release("example/app", "v1.2.3", "123", "test-token-which-is-long"), 123)
        request.assert_called_once_with("example/app", "test-token-which-is-long", "/repos/example/app/releases/123")

    def test_signing_secret_is_limited_to_release_appcast_step(self):
        workflow = ROOT / ".github/workflows/build_macos_app.yaml"
        appcast_step = subprocess.check_output(
            ["yq", "-o=json", ".jobs.package.steps[] | select(.name == \"Generate signed Sparkle appcast\")", str(workflow)],
            text=True,
        )
        step = json.loads(appcast_step)
        self.assertEqual(
            step["if"],
            "inputs.publish-release && inputs.generate-appcast && steps.config.outputs.appcast != ''",
        )
        self.assertEqual(step["env"]["SPARKLE_EDDSA_PRIVATE_KEY"], "${{ secrets.SPARKLE_EDDSA_PRIVATE_KEY }}")
        build_step = subprocess.check_output(
            ["yq", "-o=json", ".jobs.package.steps[] | select(.name == \"Build and verify\")", str(workflow)],
            text=True,
        )
        self.assertNotIn("SPARKLE_EDDSA_PRIVATE_KEY", build_step)
        self.assertEqual(json.loads(build_step)["env"]["MACOS_PACKAGE_RELEASE_ASSETS"],
                         "${{ inputs.publish-release && 'true' || 'false' }}")

    def test_native_workflows_prepare_metal_inside_the_existing_job(self):
        for path in (ROOT / ".github/workflows/build_macos_app.yaml",
                     ROOT / ".github/workflows/ci_swift.yaml"):
            output = subprocess.check_output(
                ["yq", "-o=json", '.jobs[].steps[] | select(.name == "Ensure Metal toolchain")', str(path)],
                text=True,
            )
            step = json.loads(output)
            self.assertIn("ensure-toolchain", step["run"])
            self.assertIn("--toolchain metal-toolchain", step["run"])
            self.assertIn("$SCOTTY_TOOLCHAIN_REQUEST_KEY", step["run"])
            self.assertIn("xcodebuild -downloadComponent MetalToolchain", step["run"])
            self.assertIn("github-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}",
                          step["env"]["SCOTTY_TOOLCHAIN_REQUEST_KEY"])

    def test_unattended_swift_lane_is_explicit_and_passes_values_as_arguments(self):
        workflow = ROOT / ".github/workflows/ci_swift.yaml"
        steps = json.loads(subprocess.check_output(
            ["yq", "-o=json", ".jobs.build-and-test.steps", str(workflow)], text=True))
        lane = next(step for step in steps if step.get("name") == "Run Scotty unattended verification")
        self.assertEqual(lane["if"], "inputs.scotty-unattended")
        self.assertIn('arguments=(verify-action --project "$XCODE_PROJECT"', lane["run"])
        self.assertIn('"${arguments[@]}"', lane["run"])
        self.assertNotIn("${{ inputs.", lane["run"])
        self.assertEqual(lane["timeout-minutes"], 18)
        for name in ("Build", "Test"):
            self.assertIn("!inputs.scotty-unattended", next(step for step in steps if step.get("name") == name)["if"])

    def test_swift_ci_resolves_and_reuses_a_test_destination(self):
        workflow = ROOT / ".github/workflows/ci_swift.yaml"
        resolve_step = json.loads(subprocess.check_output(
            ["yq", "-o=json", '.jobs.build-and-test.steps[] | select(.id == "xcode")', str(workflow)],
            text=True,
        ))
        self.assertIn("SUPPORTED_PLATFORMS", resolve_step["run"])
        self.assertIn("xcrun simctl list devices available --json", resolve_step["run"])
        self.assertIn("xcrun simctl create", resolve_step["run"])
        self.assertIn("destination=$destination", resolve_step["run"])

        steps = json.loads(subprocess.check_output(
            ["yq", "-o=json", ".jobs.build-and-test.steps", str(workflow)],
            text=True,
        ))
        for name in ("Build", "Test"):
            run = next(step["run"] for step in steps if step.get("name") == name)
            self.assertIn('-destination "${{ steps.xcode.outputs.destination }}"', run)
        cleanup = next(step for step in steps if step.get("name") == "Delete ephemeral iOS Simulator")
        self.assertEqual(cleanup["if"], "always() && steps.xcode.outputs.simulator-id != ''")
        self.assertIn("xcrun simctl delete", cleanup["run"])

        job = json.loads(subprocess.check_output(
            ["yq", "-o=json", ".jobs.build-and-test", str(workflow)],
            text=True,
        ))
        self.assertEqual(job["timeout-minutes"], 20)

    def test_release_reuses_the_build_job_without_artifact_transfer(self):
        build = ROOT / ".github/workflows/build_macos_app.yaml"
        publish = ROOT / ".github/workflows/publish_macos_app.yaml"
        ci_caller = ROOT / "consumers/workflows/macos-app.yaml"
        release_caller = ROOT / "consumers/workflows/publish-macos-app.yaml"
        build_jobs = json.loads(subprocess.check_output(["yq", "-o=json", ".jobs", str(build)], text=True))
        publish_jobs = json.loads(subprocess.check_output(["yq", "-o=json", ".jobs", str(publish)], text=True))
        self.assertEqual(list(build_jobs), ["package"])
        self.assertEqual(list(publish_jobs), ["package"])
        self.assertNotIn("permissions", build_jobs["package"])
        self.assertEqual(publish_jobs["package"]["permissions"], {"contents": "write"})
        self.assertEqual(publish_jobs["package"]["with"]["publish-release"], True)
        self.assertEqual(
            publish_jobs["package"]["uses"],
            "jr200-labs/github-action-templates/.github/workflows/build_macos_app.yaml@master",
        )
        self.assertFalse(any("actions/upload-artifact" in step.get("uses", "")
                             for step in build_jobs["package"]["steps"]))
        self.assertEqual(
            subprocess.check_output(["yq", "-r", ".permissions.contents", str(ci_caller)], text=True).strip(),
            "read",
        )
        self.assertEqual(
            subprocess.check_output(["yq", "-r", ".permissions.contents", str(release_caller)], text=True).strip(),
            "write",
        )
        self.assertNotIn("publish-runner:", release_caller.read_text())
        publish_text = publish.read_text()
        self.assertNotIn("actions/download-artifact", publish_text)
        self.assertNotIn("actions/upload-artifact", publish_text)
        self.assertNotIn("/releases/tags/", publish_text)
        self.assertNotIn("gh release ", publish_text)
        build_text = build.read_text()
        self.assertEqual(build_text.count("uses: jr200-labs/github-action-templates/actions/macos-release@master"), 2)
        self.assertNotIn("gh api", build_text)
        self.assertNotIn("jq ", build_text)
        self.assertNotIn("curl ", build_text)
        action = (ROOT / "actions/macos-release/action.yml").read_text()
        self.assertIn('python3 "$GITHUB_ACTION_PATH/release.py"', action)


if __name__ == "__main__":
    unittest.main()

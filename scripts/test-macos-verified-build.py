"""Exercise exact-revision reuse and fail-closed publication, offline and on macOS."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verified_build", ROOT / "actions/macos-verified-build/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class VerifiedBuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = Path.cwd()
        os.chdir(temporary.name)
        self.addCleanup(os.chdir, previous)
        self.config = {"app": "dist/Example.app", "artifact": "example-macos", "build": ["fixture-build"]}
        self.expected = {"schema": 1, "source_sha": "a" * 40, "source_tree": "e" * 40,
                         "source_identity": "commit", "config_sha256": "b" * 64,
                         "architecture": "arm64", "app": self.config["app"], "artifact": self.config["artifact"]}
        self.retained, self.output = Path("retained"), Path("release")
        self.artifact = {"id": 7, "name": build.artifact_name(self.expected), "expired": False,
                         "workflow_run": {"id": 11, "head_sha": "a" * 40,
                                          "repository_id": 23, "head_repository_id": 23}}
        self.run = {"status": "completed", "conclusion": "success", "head_sha": "a" * 40,
                    "path": ".github/workflows/macos-app.yaml", "event": "workflow_dispatch",
                    "repository": {"full_name": "example/app"}, "head_repository": {"full_name": "example/app"}}

    def lookup(self, artifacts=None):
        calls = []
        def api(repository, token, path):
            calls.append(path)
            if path.startswith("actions/artifacts?"):
                return {"artifacts": artifacts if artifacts is not None else [self.artifact]}
            if path.startswith("git/commits/"):
                return {"sha": path.rsplit("/", 1)[1], "tree": {"sha": "e" * 40}}
            return self.run
        with patch.object(build, "api", side_effect=api):
            result = build.lookup("example/app", "token", self.expected, 99)
        return result, calls

    def write_archive(self, members=None):
        self.retained.mkdir(exist_ok=True)
        with zipfile.ZipFile(self.retained / "app.zip", "w") as archive:
            for name, content in members or [("Example.app/Contents/Info.plist", plistlib.dumps(
                    {"CFBundleShortVersionString": "1.2.3"}))]:
                archive.writestr(name, content)
        receipt = dict(self.expected, version="1.2.3", archive_sha256=build.digest(self.retained / "app.zip"))
        (self.retained / "receipt.json").write_text(json.dumps(receipt))
        return receipt

    def restore(self):
        build.restore(self.config, self.expected, self.retained, self.output, "v1.2.3",
                      "example/app", "token", "a" * 40, "pull_request")

    def extract(self, command, **kwargs):
        if command[0] == "/usr/bin/ditto":
            with zipfile.ZipFile(command[-2]) as archive:
                archive.extractall(command[-1])
        return subprocess.CompletedProcess(command, 0)

    def test_exact_successful_run_is_selected(self):
        for event in ("workflow_dispatch", "push", "pull_request"):
            with self.subTest(event=event):
                self.run["event"] = event
                result, _ = self.lookup()
                self.assertEqual(result, {"artifact-id": "7", "run-id": "11", "source-sha": "a" * 40, "source-event": event})

    def test_missing_expired_current_fork_or_wrong_revision_is_a_miss(self):
        self.assertIsNone(self.lookup([])[0])
        for field, value in (("expired", True), ("name", "wrong")):
            with self.subTest(field=field), patch.dict(self.artifact, {field: value}):
                self.assertEqual(self.lookup()[0], None)
        for field, value in (("id", 99), ("head_sha", "c" * 40), ("head_repository_id", 24), ("repository_id", None)):
            with self.subTest(field=field), patch.dict(self.artifact["workflow_run"], {field: value}):
                result, calls = self.lookup()
                self.assertIsNone(result)
                self.assertEqual(len(calls), 1, "Ineligible provenance must not reach the run API")

    def test_failed_running_foreign_or_unrelated_workflow_cannot_be_published(self):
        for field, value in (("status", "in_progress"), ("conclusion", "failure"), ("conclusion", "cancelled"),
                             ("head_sha", "c" * 40), ("path", ".github/workflows/other.yaml"),
                             ("event", "pull_request_target"), ("repository", {"full_name": "other/app"}),
                             ("head_repository", {"full_name": "fork/app"})):
            with self.subTest(field=field), patch.dict(self.run, {field: value}):
                self.assertIsNone(self.lookup()[0])

    def test_lookup_api_failure_is_not_a_cache_miss(self):
        with patch.object(build, "api", side_effect=RuntimeError("HTTP 403")):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                build.lookup("example/app", "token", self.expected, 99)

    def test_lookup_checks_later_pages(self):
        with patch.object(build, "api", side_effect=[{"artifacts": [dict(self.artifact, expired=True)] * 100},
                                                   {"artifacts": [self.artifact]}, self.run]) as api:
            self.assertEqual(build.lookup("example/app", "token", self.expected, 99)["artifact-id"], "7")
            self.assertIn("page=2", api.call_args_list[1].args[2])

    def test_release_waits_for_matching_active_ci_and_does_not_duplicate_its_build(self):
        active = dict(self.run, id=11, status="in_progress")
        found = {"artifact-id": "7", "run-id": "11"}
        with patch.object(build, "lookup", side_effect=[None, found]), patch.object(build, "api", return_value={"workflow_runs": [active]}), patch.object(build.time, "sleep") as sleep:
            self.assertEqual(build.find_build("example/app", "token", self.expected, 99), found)
            sleep.assert_called_once()

    def test_release_waits_for_identical_tree_despite_a_different_pr_sha(self):
        self.expected["source_identity"] = "tree"
        self.expected["source_sha"] = "f" * 40
        active = dict(self.run, id=11, status="in_progress")
        found = {"artifact-id": "7", "run-id": "11"}
        with patch.object(build, "lookup", side_effect=[None, found]), patch.object(build, "api", return_value={"workflow_runs": [active]}), patch.object(build, "commit_tree", return_value="e" * 40), patch.object(build.time, "sleep") as sleep:
            self.assertEqual(build.find_build("example/app", "token", self.expected, 99), found)
            sleep.assert_called_once()

    def test_release_does_not_wait_for_queued_foreign_failed_or_different_builds(self):
        for overrides in ({"status": "queued"}, {"status": "completed", "conclusion": "failure"},
                          {"head_sha": "f" * 40}, {"head_repository": {"full_name": "fork/app"}}, {"id": 99}):
            active = dict(self.run, id=11, status="in_progress")
            active.update(overrides)
            with self.subTest(overrides=overrides), patch.object(build, "lookup", return_value=None), patch.object(build, "api", return_value={"workflow_runs": [active]}), patch.object(build.time, "sleep") as sleep:
                self.assertIsNone(build.find_build("example/app", "token", self.expected, 99))
                sleep.assert_not_called()

    def test_wait_is_bounded_and_failed_build_becomes_a_miss(self):
        active = dict(self.run, id=11, status="in_progress")
        with patch.object(build, "lookup", return_value=None), patch.object(build, "api", return_value={"workflow_runs": [active]}), patch.object(build.time, "monotonic", side_effect=[0, 1200]), patch.object(build.time, "sleep") as sleep:
            self.assertIsNone(build.find_build("example/app", "token", self.expected, 99))
            sleep.assert_not_called()
        with patch.object(build, "lookup", return_value=None), patch.object(build, "api", side_effect=[{"workflow_runs": [active]}, {"workflow_runs": []}]), patch.object(build.time, "sleep") as sleep:
            self.assertIsNone(build.find_build("example/app", "token", self.expected, 99))
            sleep.assert_called_once()

    def test_identity_changes_for_commit_configuration_or_architecture(self):
        original = build.artifact_name(self.expected)
        for key, value in (("source_sha", "c" * 40), ("config_sha256", "d" * 64), ("architecture", "x86_64")):
            self.assertNotEqual(original, build.artifact_name(dict(self.expected, **{key: value})))

    def test_policy_defaults_to_commit_and_rejects_unknown_identity(self):
        Path(".github").mkdir()
        Path(".github/macos-app.json").write_text(json.dumps(self.config))
        with patch.object(build.subprocess, "check_output", side_effect=["a" * 40, "e" * 40]), patch.object(build.subprocess, "run"):
            _, expected = build.identity(".github/macos-app.json")
        self.assertEqual(expected["source_identity"], "commit")
        for policy in ({"source_identity": "tree"}, {"source_identity": "nearby"}, {"source_identity": "tree", "extra": True}):
            Path(".github/macos-build-reuse.json").write_text(json.dumps(policy))
            with self.subTest(policy=policy), patch.object(build.subprocess, "check_output", side_effect=["a" * 40, "e" * 40]), patch.object(build.subprocess, "run"):
                if policy == {"source_identity": "tree"}:
                    self.assertEqual(build.identity(".github/macos-app.json")[1]["source_identity"], "tree")
                else:
                    with self.assertRaisesRegex(ValueError, "commit or tree"):
                        build.identity(".github/macos-app.json")

    def test_tree_policy_reuses_identical_release_pr_source_after_squash_merge(self):
        self.expected["source_identity"] = "tree"
        self.expected["source_sha"] = "f" * 40
        self.artifact["name"] = build.artifact_name(self.expected)
        self.assertEqual(self.lookup()[0]["artifact-id"], "7")
        self.assertEqual(build.artifact_name(self.expected),
                         build.artifact_name(dict(self.expected, source_sha="a" * 40)))
        self.assertNotEqual(build.artifact_name(self.expected),
                            build.artifact_name(dict(self.expected, source_tree="c" * 40)))

    def test_tree_policy_rejects_different_workflow_source(self):
        self.expected["source_identity"] = "tree"
        self.artifact["name"] = build.artifact_name(self.expected)
        with patch.object(build, "commit_tree", return_value="c" * 40):
            self.assertIsNone(self.lookup()[0])

    def test_tree_restore_verifies_both_receipt_and_server_source_tree(self):
        self.expected["source_identity"] = "tree"
        receipt = self.write_archive()
        receipt["source_sha"] = "c" * 40
        (self.retained / "receipt.json").write_text(json.dumps(receipt))
        with patch.object(build, "commit_identity", return_value=("f" * 40, ["b" * 40, "a" * 40])):
            with self.assertRaisesRegex(ValueError, "source tree differs"):
                self.restore()
        with patch.object(build, "commit_identity", return_value=("e" * 40, ["b" * 40, "a" * 40])), patch.object(build.subprocess, "run", side_effect=self.extract):
            self.restore()
        self.assertTrue((self.output / "example-macos.zip").is_file())

    def test_pr_merge_receipt_must_be_anchored_to_the_workflow_head(self):
        self.expected["source_identity"] = "tree"
        receipt = self.write_archive()
        receipt["source_sha"] = "c" * 40
        (self.retained / "receipt.json").write_text(json.dumps(receipt))
        for parents in ([], ["a" * 40], ["a" * 40, "b" * 40], ["b" * 40, "f" * 40]):
            with self.subTest(parents=parents), patch.object(build, "commit_identity", return_value=("e" * 40, parents)), patch.object(build.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "selected workflow source"):
                    self.restore()
                run.assert_not_called()

    def test_release_pr_head_can_differ_from_the_tested_merge_tree(self):
        self.expected["source_identity"] = "tree"
        self.artifact["name"] = build.artifact_name(self.expected)
        self.run["event"] = "pull_request"
        with patch.object(build, "commit_tree", return_value="f" * 40):
            self.assertEqual(self.lookup()[0]["artifact-id"], "7")
        active = dict(self.run, id=11, status="in_progress", head_branch="release-please--branches--main")
        with patch.object(build, "lookup", side_effect=[None, {"artifact-id": "7"}]), patch.object(build, "api", return_value={"workflow_runs": [active]}), patch.object(build, "commit_tree", return_value="f" * 40), patch.object(build.time, "sleep") as sleep:
            self.assertEqual(build.find_build("example/app", "token", self.expected, 99)["artifact-id"], "7")
            sleep.assert_called_once()

    def test_restore_creates_release_assets_without_running_the_build(self):
        self.write_archive()
        with patch.object(build.subprocess, "run", side_effect=self.extract) as run:
            self.restore()
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["/usr/bin/ditto", "/usr/bin/codesign"])
        self.assertEqual((self.output / "example-macos.zip").read_bytes(), (self.retained / "app.zip").read_bytes())
        self.assertEqual((self.output / "example-macos.zip.sha256").read_text(),
                         build.digest(self.retained / "app.zip") + "  example-macos.zip\n")

    def test_wrong_receipt_identity_cannot_create_release_assets(self):
        for field, value in (("source_sha", "c" * 40), ("config_sha256", "d" * 64), ("architecture", "x86_64"),
                             ("schema", 2), ("app", "Other.app"), ("artifact", "other"), ("archive_sha256", "bad")):
            receipt = self.write_archive()
            receipt[field] = value
            (self.retained / "receipt.json").write_text(json.dumps(receipt))
            with self.subTest(field=field), patch.object(build.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "identity or archive checksum"):
                    self.restore()
                run.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_corrupted_archive_and_missing_receipt_fail_closed(self):
        self.write_archive()
        (self.retained / "app.zip").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "identity or archive checksum"):
            self.restore()
        (self.retained / "receipt.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.restore()
        self.assertFalse(self.output.exists())

    def test_tag_and_restored_version_must_match_receipt(self):
        receipt = self.write_archive()
        with self.assertRaisesRegex(ValueError, "release tag"):
            build.restore(self.config, self.expected, self.retained, self.output, "v9.9.9")
        receipt["version"] = "9.9.9"
        (self.retained / "receipt.json").write_text(json.dumps(receipt))
        with patch.object(build.subprocess, "run", side_effect=self.extract):
            with self.assertRaisesRegex(ValueError, "differs from its receipt"):
                build.restore(self.config, self.expected, self.retained, self.output, "v9.9.9")
        self.assertFalse(self.output.exists())

    def test_bad_signature_cannot_create_release_assets(self):
        self.write_archive()
        def run(command, **kwargs):
            if command[0] == "/usr/bin/codesign":
                raise subprocess.CalledProcessError(1, command)
            return self.extract(command, **kwargs)
        with patch.object(build.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                self.restore()
        self.assertFalse(self.output.exists())

    def test_archive_rejects_traversal_and_symlink_writes_before_extraction(self):
        for path in ("../escape", "/escape", "Other.app/Contents/Info.plist", "Example.app/../../escape"):
            with self.subTest(path=path):
                self.write_archive([(path, b"bad")])
                with patch.object(build.subprocess, "run") as run:
                    with self.assertRaisesRegex(ValueError, "unexpected path"):
                        self.restore()
                    run.assert_not_called()
        link = zipfile.ZipInfo("Example.app/Contents/Link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for target, child in ((b"../../../outside", False), (b"/tmp/outside", False), (b"Internal", True)):
            members = [(link, target)]
            if child:
                members.append(("Example.app/Contents/Link/file", b"bad"))
            self.write_archive(members)
            with patch.object(build.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.restore()
                run.assert_not_called()

    def test_existing_release_output_is_never_overwritten(self):
        self.write_archive()
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "must be new"):
            self.restore()

    @unittest.skipUnless(sys.platform == "darwin", "Requires native macOS signature and archive tools")
    def test_native_roundtrip_preserves_signature_modes_and_symlinks(self):
        app = Path(self.expected["app"])
        executable = app / "Contents/MacOS/Example"
        executable.parent.mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleShortVersionString": "1.2.3", "CFBundleVersion": "1.2.3",
            "CFBundleExecutable": "Example", "CFBundleIdentifier": "org.example.reuse-check",
            "CFBundlePackageType": "APPL",
        }))
        Path("main.c").write_text("int main(void) { return 0; }\n")
        subprocess.run(["xcrun", "clang", "main.c", "-o", str(executable)], check=True)
        resources = app / "Contents/Resources"
        resources.mkdir()
        (resources / "value").write_text("fixture")
        (resources / "link").symlink_to("value")
        versions = resources / "Versions"
        (versions / "A").mkdir(parents=True)
        (versions / "A/value").write_text("versioned fixture")
        (versions / "Current").symlink_to("A")
        (resources / "current-value").symlink_to("Versions/Current/value")
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        build.record(self.expected, self.retained)
        self.restore()
        destination = Path("roundtrip")
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(self.output / "example-macos.zip"), str(destination)], check=True)
        restored = destination / "Example.app"
        self.assertEqual(build.app_version(restored), "1.2.3")
        self.assertTrue(os.access(restored / "Contents/MacOS/Example", os.X_OK))
        self.assertTrue((restored / "Contents/Resources/link").is_symlink())
        self.assertEqual((restored / "Contents/Resources/link").read_text(), "fixture")
        self.assertEqual((restored / "Contents/Resources/current-value").read_text(), "versioned fixture")


if __name__ == "__main__":
    unittest.main()

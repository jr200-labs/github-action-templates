"""Execute the workflow's real refresh shell with local command doubles."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STEP = subprocess.check_output([
    "yq", "-r", '.jobs."release-please".steps[] | select(.name == "Refresh uv.lock on release PR") | .run',
    str(ROOT / ".github/workflows/release_please.yaml"),
], text=True)
# Credentials are covered by lint-release-please-uv-lock-auth.sh. Never touch
# the test operator's credential files while exercising branch selection.
STEP = "\n".join(line for line in STEP.splitlines() if ".netrc" not in line)
DOUBLES = r'''
git() {
  printf 'git %s\n' "$*" >> calls
  case "$1" in
    diff) return 1 ;;
    ls-files) printf 'uv.lock\npackages/example/uv.lock\n' ;;
  esac
}
gh() {
  if [ "$1 $2" = 'pr view' ]; then
    jq -c --argjson number "$3" '.[] | select(.number == $number)' <<< "$LIVE_PRS"
  fi
}
uv() {
  printf 'uv %s\n' "$*" >> "$TEST_CALLS"
  return "${UV_FAILURE:-0}"
}
'''


def pr(number, component=None):
    branch = "release-please--branches--master"
    if component:
        branch += "--components--" + component
    return dict(number=number, headBranchName=branch, baseBranchName="master")


class LockfileRefreshTest(unittest.TestCase):
    def run_refresh(self, first, retry=(), *, missing=False, uv_failure=0):
        live = [dict(number=p["number"], headRefName=p["headBranchName"],
                     baseRefName="master", state="OPEN", isCrossRepository=False)
                for p in [*first, *retry]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for prefix in (root, root / "packages/example"):
                prefix.mkdir(parents=True, exist_ok=True)
                (prefix / "pyproject.toml").touch()
                (prefix / "uv.lock").touch()
            env = dict(os.environ, FIRST_PRS=json.dumps(first), RETRY_PRS=json.dumps(retry),
                       TARGET_BRANCH="master", GITHUB_REPOSITORY="example/library", GH_TOKEN="fixture",
                       LIVE_PRS=json.dumps([] if missing else live), TEST_CALLS=str(root / "calls"),
                       UV_FAILURE=str(uv_failure))
            result = subprocess.run(["bash", "-c", DOUBLES + STEP], cwd=root, env=env,
                                    text=True, capture_output=True)
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return result, calls

    def test_grouped_component_and_retry_prs_are_all_refreshed_once(self):
        first = [pr(1), pr(2, "example")]
        result, calls = self.run_refresh(first, [first[1], pr(3, "other")])
        self.assertEqual(result.returncode, 0, result.stderr)
        for item in [*first, pr(3, "other")]:
            self.assertEqual(calls.count("git push origin HEAD:" + item["headBranchName"] + "\n"), 1)
        self.assertEqual(calls.count("uv lock\n"), 6)

    def test_missing_returned_pr_and_lock_failure_are_errors(self):
        for options in (dict(missing=True), dict(uv_failure=1)):
            with self.subTest(options=options):
                result, calls = self.run_refresh([pr(2, "example")], **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("git push", calls)

    def test_no_returned_prs_is_a_legitimate_noop(self):
        result, calls = self.run_refresh([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("git push", calls)


if __name__ == "__main__":
    unittest.main()

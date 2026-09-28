"""Exercise stale release PR cleanup with local GitHub CLI doubles."""

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STEP = subprocess.check_output(
    [
        "yq",
        "-r",
        '.jobs."release-please".steps[] | select(.name == "Close stale release PRs after publish") | .run',
        str(ROOT / ".github/workflows/release_please.yaml"),
    ],
    text=True,
)
DOUBLES = r'''
gh() {
  if [ "$1 $2" = "pr list" ]; then
    printf '%s\n' "$PR_ROWS"
    return
  fi
  if [ "$1 $2" = "pr close" ]; then
    printf 'closed %s\n' "$3" >> "$TEST_CALLS"
    return
  fi
  return 1
}
'''


def encoded_row(number, title, head):
    payload = json.dumps({"number": number, "title": title, "headRefOid": head})
    return base64.b64encode(payload.encode()).decode()


class ReleasePrCleanupTest(unittest.TestCase):
    def run_cleanup(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            calls = Path(directory) / "calls"
            environment = dict(
                os.environ,
                TARGET_BRANCH="master",
                RELEASE_SHA="published-sha",
                RELEASE_TAG="v0.1.1",
                RELEASE_VERSION="0.1.1",
                PR_ROWS="\n".join(rows),
                TEST_CALLS=str(calls),
            )
            result = subprocess.run(
                ["bash", "-c", DOUBLES + STEP],
                env=environment,
                text=True,
                capture_output=True,
            )
            recorded = calls.read_text() if calls.exists() else ""
            return result, recorded

    def test_newer_release_pr_remains_open(self):
        result, calls = self.run_cleanup(
            [encoded_row(28, "chore(master): release 0.1.2", "new-release-head")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("title does not match published version 0.1.1", result.stdout)

    def test_stale_pr_for_published_version_is_closed(self):
        result, calls = self.run_cleanup(
            [encoded_row(14, "chore(master): release 0.1.1", "stale-head")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "closed 14\n")

    def test_pr_at_published_sha_remains_open(self):
        result, calls = self.run_cleanup(
            [encoded_row(14, "chore(master): release 0.1.1", "published-sha")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("head matches published release commit", result.stdout)


if __name__ == "__main__":
    unittest.main()

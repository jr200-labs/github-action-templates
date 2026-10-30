#!/usr/bin/env python3
"""Offline regressions for report-driven PR backlog notifications."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "backlog", ROOT / "scripts/report-renovate-backlog.py"
)
backlog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backlog)

REPOSITORY = "example/infrastructure"
ACTOR = "example-ci[bot]"
RUN = "https://github.com/example/infrastructure/actions/runs/123"
BODY = """An existing description and retry checkbox.

| Package | Update | Change |
|---|---|---|
| [ghcr.io/example/widget](https://example.invalid/widget) | patch | `v1.0.0` → `v1.0.1` |

- [ ] Retry
"""


def branch(result="pr-edited", target="v1.0.2", number=12):
    return {
        "branchName": "renovate/service-widget",
        "prNo": number,
        "result": result,
        "upgrades": [
            {
                "depName": "ghcr.io/example/widget",
                "packageName": "example/widget",
                "currentValue": "v1.0.0",
                "newValue": target,
                "newVersion": target,
                "packageFile": "services/widget/values.yaml",
            }
        ],
    }


class FakeGitHub:
    repository = REPOSITORY
    actor = ACTOR

    def __init__(self):
        self.pr = {
            "number": 12,
            "state": "open",
            "merged": False,
            "body": BODY,
            "title": "fix(deps): update widget to v1.0.1",
            "user": {"login": ACTOR},
            "labels": [{"name": "dependencies"}],
            "head": {
                "ref": "renovate/service-widget",
                "sha": "abc",
                "repo": {"full_name": REPOSITORY},
            },
        }
        self.comments = []
        self.mutations = []
        self.reads = 0
        self.race = None

    def ensure_label(self):
        pass

    def pages(self, path):
        assert path == "/issues/12/comments"
        return copy.deepcopy(self.comments)

    def request(self, method, path, data=None):
        if method == "GET":
            assert path == "/pulls/12"
            self.reads += 1
            if self.race and self.reads == 2:
                self.race(self.pr)
            return copy.deepcopy(self.pr)
        self.mutations.append((method, path, data))
        if method == "PATCH" and path == "/pulls/12":
            self.pr.update(data)
        elif method == "POST" and path == "/issues/12/comments":
            self.comments.append(
                {"id": len(self.comments) + 1, "user": {"login": ACTOR}, **data}
            )
        elif method == "POST" and path == "/issues/12/labels":
            self.pr["labels"] += [{"name": name} for name in data["labels"]]
        elif method == "DELETE" and path == "/issues/12/labels/renovate-blocked":
            self.pr["labels"] = [
                item for item in self.pr["labels"] if item["name"] != backlog.LABEL
            ]
        elif method == "PATCH" and path.startswith("/issues/comments/"):
            self.comments[int(path.rsplit("/", 1)[1]) - 1].update(data)
        else:
            raise AssertionError((method, path))


class BacklogTests(unittest.TestCase):
    def test_edited_pr_with_newer_target_preserves_description_and_title(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch()], RUN)
        self.assertIn("Newer update waiting", api.pr["body"])
        self.assertIn("`v1.0.1` | `v1.0.2`", api.pr["body"])
        self.assertIn("external edits", api.pr["body"])
        self.assertTrue(api.pr["body"].endswith(BODY))
        self.assertEqual(api.pr["title"], "fix(deps): update widget to v1.0.1")
        self.assertEqual(len(api.comments), 1)
        self.assertEqual(
            [item["name"] for item in api.pr["labels"]], ["dependencies", backlog.LABEL]
        )

    def test_rerun_and_renovate_body_reset_do_not_repeat_notifications(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch()], RUN)
        api.mutations.clear()
        backlog.reconcile(api, [branch()], RUN + "4")
        self.assertEqual(api.mutations, [])
        api.pr["body"] = BODY  # Renovate may regenerate its description.
        backlog.reconcile(api, [branch()], RUN + "5")
        self.assertEqual(len(api.comments), 1)
        backlog.reconcile(api, [branch(target="v1.0.3")], RUN + "6")
        self.assertEqual(len(api.comments), 2)

    def test_changed_blocker_notifies_but_same_target_alone_is_not_a_backlog(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch(target="v1.0.1")], RUN)
        self.assertIn("update blocked", api.pr["body"])
        self.assertNotIn("Newer update waiting", api.pr["body"])
        backlog.reconcile(api, [branch(result="error", target="v1.0.1")], RUN)
        self.assertEqual(len(api.comments), 2)
        self.assertIsNone(backlog.status(branch(result="done", target="v1.0.1"), BODY))

    def test_healthy_pr_does_not_get_a_warning(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch(result="done", target="v1.0.1")], RUN)
        self.assertEqual(api.mutations, [])

    def test_successful_recovery_cleans_owned_section_and_label_then_can_notify_again(
        self,
    ):
        api = FakeGitHub()
        backlog.reconcile(api, [branch()], RUN)
        backlog.reconcile(api, [branch(result="done", target="v1.0.1")], RUN)
        self.assertEqual(api.pr["body"], BODY)
        self.assertEqual(api.pr["labels"], [{"name": "dependencies"}])
        self.assertIn(backlog.RESOLVED, api.comments[0]["body"])
        backlog.reconcile(api, [branch()], RUN)
        self.assertEqual(len(api.comments), 2)

    def test_unknown_outcome_and_missing_target_cannot_clear_previous_warning(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch()], RUN)
        api.mutations.clear()
        unknown = branch(result="pending")
        unknown["upgrades"] = []
        backlog.reconcile(api, [unknown], RUN)
        self.assertEqual(api.mutations, [])

    def test_grouped_duplicates_are_collapsed_and_custom_body_is_honest(self):
        item = branch()
        item["upgrades"] *= 2
        text = backlog.status(item, "Custom description")
        self.assertIn("unknown (custom PR body)", text)
        self.assertNotIn("Newer update waiting", text)
        self.assertEqual(text.count("`ghcr.io/example/widget`"), 1)

    def test_partially_updated_group_still_has_a_backlog(self):
        body = BODY.replace(
            "\n\n- [ ] Retry",
            "\n| [ghcr.io/example/widget](https://example.invalid/widget) | patch | `v1.0.0` → `v1.0.2` |\n\n- [ ] Retry",
        )
        self.assertIn("Newer update waiting", backlog.status(branch(), body))
        item = branch(result="done")
        item["upgrades"].append({**item["upgrades"][0], "newValue": "v1.0.1"})
        self.assertIsNone(backlog.status(item, body))

    def test_only_current_open_same_repo_bot_owned_pr_is_changed(self):
        for mutate in [
            lambda pr: pr.update(state="closed"),
            lambda pr: pr.update(merged=True),
            lambda pr: pr["head"].update(ref="feature/other"),
            lambda pr: pr["head"]["repo"].update(full_name="other/fork"),
            lambda pr: pr["user"].update(login="someone-else"),
        ]:
            api = FakeGitHub()
            mutate(api.pr)
            backlog.reconcile(api, [branch()], RUN)
            self.assertEqual(api.mutations, [])
        for number in [None, 0, "12", True]:
            api = FakeGitHub()
            backlog.reconcile(api, [branch(number=number)], RUN)
            self.assertEqual(api.reads, 0)

    def test_close_head_change_or_body_edit_before_write_skips_mutation(self):
        for mutate in [
            lambda pr: pr.update(state="closed"),
            lambda pr: pr["head"].update(sha="new"),
            lambda pr: pr.update(body="Human edit"),
        ]:
            api = FakeGitHub()
            api.race = mutate
            backlog.reconcile(api, [branch()], RUN)
            self.assertEqual(api.mutations, [])

    def test_only_authenticated_bot_comments_dedupe_notifications(self):
        api = FakeGitHub()
        backlog.reconcile(api, [branch()], RUN)
        api.comments[0]["user"]["login"] = "someone-else"
        backlog.reconcile(api, [branch()], RUN)
        self.assertEqual(len(api.comments), 2)

    def test_latest_pass_and_exact_repository_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, branches in enumerate([[branch()], [branch(result="done")]]):
                path = Path(directory) / f"pass{index}.json"
                path.write_text(
                    json.dumps(
                        {
                            "repositories": {
                                REPOSITORY: {"branches": branches},
                                "other/repo": {"branches": [branch(number=99)]},
                            }
                        }
                    )
                )
                paths.append(path)
            self.assertEqual(
                backlog.read_branches(paths, REPOSITORY), [branch(result="done")]
            )
            paths[-1].write_text(
                json.dumps({"repositories": {REPOSITORY: {"branches": []}}})
            )
            self.assertEqual(backlog.read_branches(paths, REPOSITORY), [])
            paths[-1].write_text("{}")
            with self.assertRaises(TypeError):
                backlog.read_branches(paths, REPOSITORY)

    def test_template_uses_current_reports_before_repairs_for_targeted_and_full_runs(
        self,
    ):
        workflow = (ROOT / ".github/workflows/renovate.yaml").read_text()
        step = workflow.split("      - name: Report blocked Renovate updates\n")[
            1
        ].split("\n      - name:")[0]
        self.assertIn("if: success() && inputs.dry-run == ''", step)
        self.assertIn("pass1.report.json", step)
        self.assertIn("pass2.report.json", step)
        self.assertIn("outputs.app-slug", step)
        self.assertLess(
            workflow.index("Report blocked Renovate updates"),
            workflow.index("Force-rebase Renovate branches"),
        )
        checkout = workflow.split("      - name: Checkout shared repair scripts\n")[
            1
        ].split("\n      - name:")[0]
        self.assertNotIn("inputs.repair-branches", checkout)
        self.assertNotIn("gitIgnoredAuthors", workflow)


if __name__ == "__main__":
    unittest.main()

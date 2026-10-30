#!/usr/bin/env python3
"""Annotate existing Renovate PRs using the current run's structured report."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

START = "<!-- renovate-backlog:start -->"
END = "<!-- renovate-backlog:end -->"
NOTICE = "<!-- renovate-backlog:notice:"
RESOLVED = "<!-- renovate-backlog:resolved -->"
LABEL = "renovate-blocked"
REASONS = {
    "pr-edited": (
        "Renovate stopped updating this branch because it detected external edits.",
        "Review those edits, then request rebase/retry on this PR. Retrying can discard custom changes.",
    ),
    "error": (
        "Renovate could not complete the update.",
        "Inspect the linked Renovate run and repair the failure.",
    ),
    "update-not-scheduled": (
        "The update is waiting for its configured schedule.",
        "Wait for the update window or review the schedule.",
    ),
    "not-scheduled": (
        "The update is waiting for its configured schedule.",
        "Wait for the update window or review the schedule.",
    ),
    "needs-approval": (
        "The update requires approval.",
        "Review the Dependency Dashboard approval request.",
    ),
    "needs-pr-approval": (
        "The update requires approval.",
        "Review the Dependency Dashboard approval request.",
    ),
}
SUCCESS = {"done", "pr-created", "already-existed", "no-work"}


def without_status(body):
    return re.sub(
        re.escape(START) + r".*?" + re.escape(END) + r"\n*", "", body, flags=re.DOTALL
    )


def cell_text(value):
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    return value.replace("`", "").strip()


def proposals(body):
    """Read standard Renovate Package/Change tables; never infer from a PR title."""
    result = {}
    headers = []
    for line in without_status(body).splitlines():
        if not line.startswith("|"):
            headers = []
            continue
        cells = [cell_text(cell) for cell in line.strip().strip("|").split("|")]
        if "Package" in cells and ("Change" in cells or "New value" in cells):
            headers = cells
            continue
        if not headers or len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        value = row.get("New value") or row.get("Change", "").rsplit("→", 1)[-1].strip()
        if value and not re.fullmatch(r"[-: ]+", value):
            result.setdefault(row["Package"], set()).add(value)
    return result


def inline(value):
    # Package metadata is untrusted Markdown. Suppress mentions and table/HTML injection.
    value = str(value).replace("@", "@\u200b")
    return "`" + re.sub(r"[\r\n`|<>]", " ", value) + "`"


def status(branch, body):
    proposed = proposals(body)
    rows = set()
    behind = False
    targets = {}
    aliases = {}
    for upgrade in branch.get("upgrades", []):
        target = upgrade.get("newValue") or upgrade.get("newVersion")
        name = upgrade.get("depName") or upgrade.get("packageName")
        if name and target:
            targets.setdefault(name, set()).add(str(target))
            aliases[name] = upgrade.get("packageName")
    for name, eligible in targets.items():
        values = proposed.get(name) or proposed.get(aliases[name])
        # Unknown/custom bodies are explicitly unknown, rather than a guessed backlog.
        current = ", ".join(sorted(values)) if values else "unknown (custom PR body)"
        if values and values != eligible:
            behind = True
        rows.add((name, current, ", ".join(sorted(eligible))))
    reason_action = REASONS.get(branch.get("result"))
    if not reason_action and not behind:
        return None
    if not reason_action:
        reason_action = (
            "This PR has not caught up with the latest eligible update detected by Renovate.",
            "Review the Renovate run and request rebase/retry if appropriate.",
        )
    if not rows:
        return None
    heading = "⚠️ Newer update waiting" if behind else "⚠️ Renovate update blocked"
    reason, action = reason_action
    lines = [
        f"### {heading}",
        "",
        reason,
        "",
        "| Package | Proposed in this PR | Latest eligible target |",
        "|---|---|---|",
    ]
    lines += [
        "| " + " | ".join(inline(cell) for cell in row) + " |" for row in sorted(rows)
    ]
    lines += [
        "",
        f"**Action:** {action}",
        "",
        "Targets reflect this run’s Renovate policy and filters; this is not a count of intervening releases.",
    ]
    return "\n".join(lines)


def read_branches(paths, repository):
    branches = {}
    for path in paths:
        report = json.loads(Path(path).read_text())
        entry = report.get("repositories", {}).get(repository)
        if not isinstance(entry, dict) or not isinstance(entry.get("branches"), list):
            raise TypeError("Renovate report lacks branch evidence for this repository")
        branches = {}
        for branch in entry["branches"]:
            if not isinstance(branch, dict) or not isinstance(
                branch.get("branchName"), str
            ):
                raise TypeError("Invalid Renovate branch report")
            # Later passes supersede earlier observations, including successful recovery.
            branches[branch["branchName"]] = branch
    return list(branches.values())


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository = repository
        self.prefix = "https://api.github.com/repos/" + repository
        self.token = token
        self.actor = os.environ["RENOVATE_REPORT_ACTOR"]

    def request(self, method, path, data=None):
        url = path if path.startswith("https://api.github.com/") else self.prefix + path
        payload = json.dumps(data).encode() if data is not None else None
        request = Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as error:
            # Do not emit response bodies, request headers, or confidential author identities.
            raise RuntimeError(f"GitHub {method} failed (HTTP {error.code})") from None

    def pages(self, path):
        for page in range(1, 1001):
            items = self.request("GET", f"{path}?per_page=100&page={page}")
            yield from items
            if len(items) < 100:
                return
        raise RuntimeError("GitHub pagination limit exceeded")

    def ensure_label(self):
        try:
            self.request(
                "POST",
                "/labels",
                {
                    "name": LABEL,
                    "color": "D93F0B",
                    "description": "Renovate is blocked or a newer eligible update is waiting",
                },
            )
        except RuntimeError as error:
            if "HTTP 422" not in str(error):
                raise
            # Only an already existing label is safe to ignore.
            self.request("GET", "/labels/" + LABEL)


def reconcile(api, branches, run_url):
    for branch in branches:
        number = branch.get("prNo")
        if type(number) is not int or number <= 0:
            continue
        path = f"/pulls/{number}"
        pr = api.request("GET", path)
        if (
            pr.get("state") != "open"
            or pr.get("merged")
            or pr["head"]["ref"] != branch["branchName"]
            or pr["head"]["repo"]["full_name"].lower() != api.repository.lower()
            or pr["user"]["login"] != api.actor
        ):
            continue
        body = pr.get("body") or ""
        content = status(branch, body)
        labels = {label["name"] for label in pr.get("labels", [])}
        if content is None and branch.get("result") not in SUCCESS:
            # Partial, deferred or unknown outcomes cannot prove recovery.
            continue
        clean = without_status(body)
        new_body = (
            f"{START}\n{content}\n\n[Renovate run]({run_url})\n{END}\n\n{clean}"
            if content
            else clean
        )
        # Keep the run link stable when only the observing run changed.
        old_section = re.search(
            re.escape(START) + r"\n(.*?)\n\n\[Renovate run\]", body, re.DOTALL
        )
        if content and old_section and old_section[1] == content:
            new_body = body
        comments = (
            list(api.pages(f"/issues/{number}/comments"))
            if content or LABEL in labels or START in body
            else []
        )
        fingerprint = (
            hashlib.sha256(content.encode()).hexdigest()[:24] if content else None
        )
        notices = [
            comment
            for comment in comments
            if comment["user"]["login"] == api.actor
            and NOTICE in (comment.get("body") or "")
        ]
        latest = notices[-1]["body"] if notices else ""
        marker = f"{NOTICE}{fingerprint} -->"
        # Recheck immediately before mutation: never annotate closed/merged or changed PRs.
        fresh = api.request("GET", path)
        if (
            fresh.get("state") != "open"
            or fresh.get("merged")
            or fresh["head"]["sha"] != pr["head"]["sha"]
            or fresh.get("body") != pr.get("body")
        ):
            continue
        if new_body != body:
            api.request("PATCH", path, {"body": new_body})
        if content:
            if LABEL not in labels:
                api.ensure_label()
                api.request("POST", f"/issues/{number}/labels", {"labels": [LABEL]})
            if marker not in latest or RESOLVED in latest:
                api.request(
                    "POST",
                    f"/issues/{number}/comments",
                    {"body": f"{marker}\n{content}\n\n[Renovate run]({run_url})"},
                )
        else:
            if LABEL in labels:
                api.request("DELETE", f"/issues/{number}/labels/{quote(LABEL)}")
            if notices and RESOLVED not in latest:
                api.request(
                    "PATCH",
                    f"/issues/comments/{notices[-1]['id']}",
                    {
                        "body": latest
                        + f"\n\n{RESOLVED}\nResolved in the [Renovate run]({run_url})."
                    },
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    if not run_id.isdigit():
        raise ValueError("Invalid run ID")
    branches = read_branches(args.reports, repository)
    api = GitHub(repository, os.environ["RENOVATE_REPORT_TOKEN"])
    reconcile(api, branches, f"https://github.com/{repository}/actions/runs/{run_id}")
    print("Renovate backlog status reconciled for reported PRs.")


if __name__ == "__main__":
    main()

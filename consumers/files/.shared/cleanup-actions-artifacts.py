#!/usr/bin/env python3
"""Prune completed-run artifacts older than the enforced five-day ceiling."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
import urllib.error
import urllib.request


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        self.prefix = "https://api.github.com/repos/" + repository
        self.token = token

    def request(self, path, method="GET"):
        request = urllib.request.Request(self.prefix + path, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise RuntimeError(f"GitHub {method} {path} failed with status {error.code}") from None

    def artifacts(self):
        result = []
        for page in range(1, 1001):
            data = self.request(f"/actions/artifacts?per_page=100&page={page}")
            if data is None or not isinstance(data.get("artifacts"), list):
                raise RuntimeError("artifact inventory is unavailable or incomplete")
            result.extend(data["artifacts"])
            if len(data["artifacts"]) < 100:
                return result
        raise RuntimeError("artifact inventory exceeded page limit; no deletion performed")


def prune(api, now, apply=False):
    cutoff = now - timedelta(days=5)
    candidates = []
    # Finish inventory and validate run states before the first mutation.
    for item in api.artifacts():
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        if created > cutoff or item.get("expired"):
            continue
        run_id = item.get("workflow_run", {}).get("id")
        if not run_id:
            raise RuntimeError("artifact has no source run; no deletion performed")
        run = api.request(f"/actions/runs/{run_id}")
        if run is not None and run.get("status") == "completed":
            candidates.append(item)
    deleted, reclaimed = 0, 0
    for item in candidates:
        path = f"/actions/artifacts/{item['id']}"
        actual = api.request(path)
        if actual is None:
            continue
        if any(actual.get(key) != item.get(key) for key in ["id", "name", "size_in_bytes", "created_at"]):
            raise RuntimeError("artifact changed since inventory; deletion stopped")
        if apply:
            api.request(path, "DELETE")
            if api.request(path) is not None:
                raise RuntimeError("artifact deletion did not converge")
            deleted += 1
            reclaimed += item["size_in_bytes"]
    return {"apply": apply, "candidates": len(candidates), "deleted": deleted, "reclaimed_bytes": reclaimed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    result = prune(api, datetime.now(timezone.utc), args.apply)
    print(json.dumps(result, sort_keys=True))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as output:
            output.write(f"Actions retention: {result['deleted']} old artifacts deleted; {result['reclaimed_bytes']} bytes reclaimed.\n")


if __name__ == "__main__":
    main()

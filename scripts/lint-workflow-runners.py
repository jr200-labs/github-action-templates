#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Reject runner selection that bypasses the configured Actions profile."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import yaml


class Loader(yaml.BaseLoader):
    """Keep GitHub's `on` key and reject ambiguous duplicate mapping keys."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


PROFILE = r"fromJSON\(vars\.RUNNER_PROFILES\)\[vars\.RUNNER_PROFILE\]\.(?:default|heavy|publish|macos)"
SELECTOR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.DOTALL)


def errors_for(path):
    errors = []
    try:
        doc = yaml.load(path.read_text(), Loader=Loader)
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
            raise ValueError("workflow must contain a jobs mapping")
        events = doc.get("on", {})
        events = events if isinstance(events, dict) else {}
        call = events.get("workflow_call")
        call = call if isinstance(call, dict) else {}
        call_inputs = call.get("inputs", {})
        dispatch = events.get("workflow_dispatch")
        dispatch = dispatch if isinstance(dispatch, dict) else {}
        dispatch_inputs = dispatch.get("inputs", {})

        def fail(location, reason):
            errors.append(f"{path}:{location}: {reason}")

        def runner_input(name):
            spec = call_inputs.get(name, {})
            return (
                isinstance(spec, dict)
                and spec.get("type") == "string"
                and spec.get("default", "") == ""
                and name not in dispatch_inputs
            )

        def approved(value):
            if not isinstance(value, str):
                return False
            match = SELECTOR.fullmatch(value.strip())
            if not match:
                return False
            parts = [part.strip() for part in match[1].split("||")]
            if all(re.fullmatch(PROFILE, part) for part in parts):
                return True
            # Reusable primitives may accept a runner from a checked caller.
            match_input = re.fullmatch(r"inputs\.(runner|publish-runner)", parts[0])
            return bool(
                match_input
                and runner_input(match_input[1])
                and all(re.fullmatch(PROFILE, part) for part in parts[1:])
            )

        for name in dispatch_inputs:
            if name in {"runner", "publish-runner"}:
                fail("on.workflow_dispatch.inputs", "manual runner overrides are forbidden")
        for job_id, job in doc["jobs"].items():
            location = f"jobs.{job_id}"
            if not isinstance(job, dict):
                fail(location, "job must be a mapping")
                continue
            if "runs-on" in job:
                value = job["runs-on"]
                matrix_match = re.fullmatch(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}", value) if isinstance(value, str) else None
                if matrix_match:
                    matrix = job.get("strategy", {}).get("matrix", {})
                    if not isinstance(matrix, dict):
                        fail(location + ".strategy.matrix", "dynamic runner matrices cannot be verified")
                    else:
                        axis = matrix_match[1]
                        values = matrix.get(axis, [])
                        if not isinstance(values, list):
                            values = [values]
                        include = matrix.get("include", [])
                        if not isinstance(include, list) or any(not isinstance(row, dict) for row in include):
                            fail(location + ".strategy.matrix.include", "matrix includes must be static mappings")
                        else:
                            values += [row[axis] for row in include if axis in row]
                        if not values or not all(approved(v) for v in values):
                            fail(location + ".runs-on", "every runner matrix value must select the configured profile")
                elif not approved(value):
                    fail(location + ".runs-on", "must select from RUNNER_PROFILES[RUNNER_PROFILE]; literals, fixed profiles and arbitrary expressions are forbidden")
            elif "uses" not in job:
                fail(location, "job must declare runs-on or use a reusable workflow")
            if "uses" in job and not str(job["uses"]).startswith("./") and "runner" not in job.get("with", {}):
                fail(location + ".with.runner", "remote reusable callers must forward the configured runner profile")
            for key, value in job.get("with", {}).items():
                if key in {"runner", "publish-runner", "build-runner", "build-os"} and not approved(value):
                    fail(location + ".with." + key, "forward the configured profile; runner overrides are forbidden")
    except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
        errors.append(f"{path}: invalid workflow: {exc}")
    return errors


def policy_errors(path, profile, profiles):
    """Check a consumer's committed expectation against effective GitHub vars."""
    try:
        policy = json.loads(path.read_text())
        if set(policy) != {"profile", "roles"} or not isinstance(policy["roles"], dict) or not policy["roles"]:
            raise ValueError("policy requires profile and a nonempty roles mapping")
        if profile != policy["profile"]:
            raise ValueError(f"effective RUNNER_PROFILE={profile!r}; expected {policy['profile']!r}; remove repository variable overrides")
        actual = json.loads(profiles)[profile]
        for role, label in policy["roles"].items():
            if actual.get(role) != label:
                raise ValueError(f"effective runner for {role} differs from the committed policy; check RUNNER_PROFILES overrides")
        return []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"{path}: {exc}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default=".github/workflows")
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    directory = Path(args.directory)
    if not directory.is_dir():
        parser.error(f"workflows directory does not exist: {directory}")
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    errors = [error for path in paths for error in errors_for(path)]
    if not paths:
        errors.append(f"{directory}: no workflows found")
    if args.policy:
        errors += policy_errors(args.policy, os.environ.get("RUNNER_PROFILE", ""), os.environ.get("RUNNER_PROFILES", ""))
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"lint-workflow-runners: {len(paths)} workflows follow the configured runner profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())

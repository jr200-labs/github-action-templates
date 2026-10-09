#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Enforce a five-day ceiling for workflow and local composite artifacts."""
import argparse
from pathlib import Path
import re
import sys

import yaml

MAX_DAYS = 5


class Loader(yaml.BaseLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def valid_days(value):
    return isinstance(value, str) and re.fullmatch(r"[1-5]", value) is not None


def scan(path):
    errors = []
    try:
        data = yaml.load(path.read_text(), Loader=Loader)
        if not isinstance(data, dict):
            raise ValueError("expected a YAML mapping")
        jobs = data.get("jobs", {})
        if "runs" in data:
            jobs = {"composite": data["runs"]}
        for name, job in jobs.items():
            env = {**data.get("env", {}), **job.get("env", {})}
            for index, step in enumerate(job.get("steps", [])):
                action = str(step.get("uses", "")).split("@", 1)[0].lower()
                inputs = step.get("with", {})
                effective_env = {**env, **step.get("env", {})}
                if action in {"actions/upload-artifact", "actions/upload-pages-artifact"}:
                    days = inputs.get("retention-days")
                    # Pages' action has a documented one-day default.
                    if days is None and action == "actions/upload-pages-artifact":
                        continue
                    if not valid_days(days):
                        errors.append(f"{path}: {name} step {index + 1}: {action} requires literal retention-days: 1..{MAX_DAYS}")
                if action in {"docker/build-push-action", "docker/bake-action"}:
                    if str(effective_env.get("DOCKER_BUILD_RECORD_UPLOAD", "")).lower() == "false":
                        continue
                    if not valid_days(effective_env.get("DOCKER_BUILD_RECORD_RETENTION_DAYS")):
                        errors.append(f"{path}: {name} step {index + 1}: Docker records require literal DOCKER_BUILD_RECORD_RETENTION_DAYS: 1..{MAX_DAYS}")
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as error:
        errors.append(f"{path}: invalid workflow: {error}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="*", default=[".github/workflows", ".github/actions", "actions"])
    args = parser.parse_args()
    errors = []
    files = set()
    for directory in args.directories:
        root = Path(directory)
        if root.is_file():
            files.add(root)
        else:
            files.update(root.rglob("*.yaml"))
            files.update(root.rglob("*.yml"))
    for path in sorted(files):
        errors.extend(scan(path))
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"Artifact retention passed: {len(files)} YAML files, maximum {MAX_DAYS} days")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())

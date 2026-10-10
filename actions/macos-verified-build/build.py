"""Retain and restore verified macOS apps, bound to committed build inputs."""

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def identity(config_path):
    config = json.loads(Path(config_path).read_text())
    app = Path(config["app"])
    artifact = config["artifact"]
    if (app.is_absolute() or ".." in app.parts or app.suffix != ".app" or
            not app.resolve().is_relative_to(Path.cwd().resolve()) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", artifact)):
        raise ValueError("Invalid app configuration")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an exact Git commit")
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise ValueError("Expected an exact Git source tree")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], check=True)
    policy_path = Path(".github/macos-build-reuse.json")
    if policy_path.exists():
        subprocess.run(["git", "ls-files", "--error-unmatch", str(policy_path)],
                       check=True, stdout=subprocess.DEVNULL)
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {"source_identity": "commit"}
    if set(policy) != {"source_identity"} or policy["source_identity"] not in ("commit", "tree"):
        raise ValueError("Build reuse policy must select commit or tree source identity")
    return config, {
        "schema": 1,
        "source_sha": sha,
        "source_tree": tree,
        "source_identity": policy["source_identity"],
        "config_sha256": digest(Path(config_path)),
        "architecture": platform.machine(),
        "app": str(app),
        "artifact": artifact,
    }


def artifact_name(expected):
    # Configuration and architecture are build inputs, not just the Git revision.
    key_fields = dict(expected)
    if expected["source_identity"] == "tree":
        del key_fields["source_sha"]
    key = hashlib.sha256(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()
    return "verified-macos-" + key


def api(repository, token, path):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    request = Request(f"https://api.github.com/repos/{repository}/{path}", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-action-templates-macos-verified-build",
    })
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"GitHub build lookup failed ({getattr(error, 'code', 'network error')})") from error


def lookup(repository, token, expected, current_run):
    name = artifact_name(expected)
    # Bounded lookup. An expired/missing artifact is a cache miss; API failures
    # are errors, never silently downgraded to duplicate builds.
    for page in range(1, 11):
        result = api(repository, token, "actions/artifacts?" + urlencode({"name": name, "per_page": 100, "page": page}))
        artifacts = result["artifacts"]
        for artifact in artifacts:
            source = artifact.get("workflow_run", {})
            if (artifact.get("name") != name or artifact.get("expired") is not False or
                    source.get("id") == current_run or
                    source.get("head_repository_id") != source.get("repository_id") or
                    not source.get("repository_id")):
                continue
            head_sha = source.get("head_sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
                continue
            if expected["source_identity"] == "commit" and head_sha != expected["source_sha"]:
                continue
            run = api(repository, token, f"actions/runs/{int(source['id'])}")
            if (run.get("status") != "completed" or run.get("conclusion") != "success" or
                    run.get("head_sha") != head_sha or
                    run.get("path") != ".github/workflows/macos-app.yaml" or
                    run.get("event") not in ("push", "workflow_dispatch", "pull_request") or
                    (run.get("repository") or {}).get("full_name") != repository or
                    (run.get("head_repository") or {}).get("full_name") != repository):
                continue
            if expected["source_identity"] == "tree" and commit_tree(repository, token, head_sha) != expected["source_tree"]:
                continue
            return {"artifact-id": str(int(artifact["id"])), "run-id": str(int(source["id"]))}
        if len(artifacts) < 100:
            break
    return None


def commit_tree(repository, token, sha):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid build source commit")
    commit = api(repository, token, "git/commits/" + sha)
    tree = (commit.get("tree") or {}).get("sha")
    if commit.get("sha") != sha or not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise ValueError("GitHub returned an invalid build source identity")
    return tree


def find_build(repository, token, expected, current_run, wait_seconds=1200):
    deadline = time.monotonic() + wait_seconds
    trees = {}
    while True:
        found = lookup(repository, token, expected, current_run)
        if found:
            return found
        # Wait only for work already executing. Holding a Mac runner while
        # waiting for a queued build could prevent that build from ever starting.
        runs = api(repository, token, "actions/workflows/macos-app.yaml/runs?status=in_progress&per_page=100")["workflow_runs"]
        matching = False
        for run in runs:
            sha = run.get("head_sha", "")
            if (run.get("id") == current_run or run.get("status") != "in_progress" or
                    run.get("path") != ".github/workflows/macos-app.yaml" or
                    run.get("event") not in ("push", "workflow_dispatch", "pull_request") or
                    (run.get("repository") or {}).get("full_name") != repository or
                    (run.get("head_repository") or {}).get("full_name") != repository or
                    not re.fullmatch(r"[0-9a-f]{40}", sha)):
                continue
            if expected["source_identity"] == "commit":
                matching = sha == expected["source_sha"]
            else:
                if sha not in trees:
                    trees[sha] = commit_tree(repository, token, sha)
                matching = trees[sha] == expected["source_tree"]
            if matching:
                break
        remaining = deadline - time.monotonic()
        if not matching or remaining <= 0:
            return None
        print("Matching CI build is still running; waiting for its verified artifact", flush=True)
        time.sleep(min(15, remaining))


def app_version(app):
    if app.is_symlink() or not app.is_dir():
        raise ValueError("Verified build must contain a regular app directory")
    with (app / "Contents/Info.plist").open("rb") as stream:
        version = plistlib.load(stream).get("CFBundleShortVersionString")
    if not isinstance(version, str) or not version or "\n" in version:
        raise ValueError("Invalid app version")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=120)
    return version


def record(expected, output):
    app = Path(expected["app"])
    version = app_version(app)
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "app.zip"
    # ditto preserves executable modes, symlinks, resource forks and signatures;
    # uploading a raw .app through Actions would discard executable modes.
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)],
                   check=True, timeout=120)
    receipt = dict(expected, version=version, archive_sha256=digest(archive))
    (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")


def restore(config, expected, retained, output, release_tag, repository="", token=""):
    receipt_path, archive = retained / "receipt.json", retained / "app.zip"
    if any(path.is_symlink() or not path.is_file() for path in (receipt_path, archive)):
        raise ValueError("Verified build is missing its archive or receipt")
    receipt = json.loads(receipt_path.read_text())
    keys = set(expected) - ({"source_sha"} if expected["source_identity"] == "tree" else set())
    if (set(receipt) != set(expected) | {"version", "archive_sha256"} or
            any(receipt.get(key) != expected[key] for key in keys) or
            receipt["archive_sha256"] != digest(archive)):
        raise ValueError("Verified build identity or archive checksum does not match")
    if expected["source_identity"] == "tree" and commit_tree(repository, token, receipt["source_sha"]) != expected["source_tree"]:
        raise ValueError("Recorded build source tree differs from the release")
    if not release_tag or release_tag != config.get("tag_prefix", "v") + receipt["version"]:
        raise ValueError("Verified app version does not match the release tag")
    # Check archive member paths before invoking ditto. App-internal symlinks
    # are expected (frameworks); their validity is checked by codesign.
    with zipfile.ZipFile(archive) as bundle:
        names = set()
        links = set()
        for member in bundle.infolist():
            path = Path(member.filename)
            if (path.is_absolute() or ".." in path.parts or not path.parts or
                    path.parts[0] not in (Path(expected["app"]).name, "__MACOSX") or
                    member.filename in names):
                raise ValueError("Verified archive contains an unexpected path")
            names.add(member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                target = bundle.read(member).decode("utf-8")
                resolved = Path(posixpath.normpath(posixpath.join(str(path.parent), target)))
                if (not target or Path(target).is_absolute() or not resolved.parts or
                        resolved.parts[0] != Path(expected["app"]).name):
                    raise ValueError("Verified archive contains an escaping symlink")
                links.add(path)
        if any(parent in links for name in names for parent in Path(name).parents):
            raise ValueError("Verified archive writes through a symlink")
    if output.exists() or output.is_symlink():
        raise ValueError("Release output must be new")
    with tempfile.TemporaryDirectory(prefix="macos-restore-") as temporary:
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive.resolve()), temporary], check=True, timeout=120)
        version = app_version(Path(temporary) / Path(expected["app"]).name)
        if version != receipt["version"]:
            raise ValueError("Restored app version differs from its receipt")
    output.mkdir(parents=True, exist_ok=False)
    destination = output / (expected["artifact"] + ".zip")
    shutil.copyfile(archive, destination)
    destination.with_suffix(".zip.sha256").write_text(f"{receipt['archive_sha256']}  {destination.name}\n")
    print(f"Reused verified app built at {receipt['source_sha']} for release {expected['source_sha']}; version {version}")


def main():
    config, expected = identity(os.environ["BUILD_CONFIG"])
    mode = os.environ["BUILD_MODE"]
    retained = Path(os.environ["BUILD_RETAINED"])
    outputs = {"artifact-name": artifact_name(expected)}
    if mode == "lookup":
        artifact = find_build(os.environ["GITHUB_REPOSITORY"], os.environ["BUILD_TOKEN"], expected,
                          int(os.environ["GITHUB_RUN_ID"]))
        outputs.update(artifact or {"artifact-id": "", "run-id": ""})
        print(f"Reusing verified artifact {artifact['artifact-id']}" if artifact else "No matching completed build; build the release once")
    elif mode == "record":
        record(expected, retained)
    elif mode == "restore":
        restore(config, expected, retained, Path(os.environ["BUILD_OUTPUT"]), os.environ["BUILD_RELEASE_TAG"],
                os.environ["GITHUB_REPOSITORY"], os.environ["BUILD_TOKEN"])
    else:
        raise ValueError("Unknown verified build operation")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()

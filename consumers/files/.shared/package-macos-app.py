"""Repository-configured macOS app packaging. Python stdlib only; no signing identities."""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess


def configuration(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or set(config) - {"app", "artifact", "build", "setup_uv", "tag_prefix"}:
        raise ValueError("Unknown macOS app configuration fields")
    if not isinstance(config.get("artifact"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", config["artifact"]):
        raise ValueError("artifact must be a safe 1–80 character asset basename")
    app = config.get("app")
    if not isinstance(app, str) or Path(app).is_absolute() or ".." in Path(app).parts or Path(app).suffix != ".app":
        raise ValueError("app must be a repository-relative .app path without parent traversal")
    if not Path(app).resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("app must stay within the repository")
    build = config.get("build")
    if not isinstance(build, list) or not build or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in build):
        raise ValueError("build must be a nonempty argv array for the repository's build/test command")
    if type(config.get("setup_uv", False)) is not bool:
        raise ValueError("setup_uv must be boolean")
    if not isinstance(config.get("tag_prefix", "v"), str) or not re.fullmatch(r"[A-Za-z0-9._-]*", config.get("tag_prefix", "v")):
        raise ValueError("tag_prefix must contain only letters, digits, dots, hyphens or underscores")
    return config


def package(config, output, release_tag=""):
    app = Path(config["app"])
    if app.exists() or app.is_symlink() or output.exists():
        raise ValueError("Build app and output paths must be new; existing artifacts are never overwritten")
    subprocess.run(config["build"], check=True, timeout=1200)
    if app.is_symlink() or not app.is_dir() or not app.resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("Build must produce the configured app directory inside the repository")
    with (app / "Contents/Info.plist").open("rb") as file:
        version = plistlib.load(file).get("CFBundleShortVersionString")
    if not isinstance(version, str) or not version:
        raise ValueError("App is missing CFBundleShortVersionString")
    if release_tag and release_tag != config.get("tag_prefix", "v") + version:
        raise ValueError("App version does not match the requested release tag")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=120)
    output.mkdir(parents=True, exist_ok=False)
    archive = output / (config["artifact"] + ".zip")
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True, timeout=120)
    with archive.open("rb") as file:
        digest = hashlib.sha256()
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    archive.with_suffix(".zip.sha256").write_text(f"{digest.hexdigest()}  {archive.name}\n")
    print(f"Verified app version {version}; created {archive.name} and checksum")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("metadata", "package"))
    parser.add_argument("--config", default=".github/macos-app.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-tag", default="")
    args = parser.parse_args()
    config = configuration(args.config)
    if args.operation == "metadata":
        print("artifact=" + config["artifact"])
        print("setup_uv=" + str(config.get("setup_uv", False)).lower())
    else:
        if args.output is None:
            parser.error("package requires --output")
        package(config, args.output, args.release_tag)


if __name__ == "__main__":
    main()

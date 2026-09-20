"""Repository-configured macOS app packaging. Python stdlib only; no signing identities."""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess


def safe_argv(value, field):
    if not isinstance(value, list) or not value or any(
        not isinstance(arg, str) or not arg or "\0" in arg for arg in value
    ):
        raise ValueError(f"{field} must be a nonempty argv array")
    return value


def safe_asset(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError(f"{field} must be a safe 1–80 character asset name")
    return value


def configuration(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or set(config) - {"app", "artifact", "build", "setup_uv", "tag_prefix", "sparkle"}:
        raise ValueError("Unknown macOS app configuration fields")
    safe_asset(config.get("artifact"), "artifact")
    app = config.get("app")
    if not isinstance(app, str) or Path(app).is_absolute() or ".." in Path(app).parts or Path(app).suffix != ".app":
        raise ValueError("app must be a repository-relative .app path without parent traversal")
    if not Path(app).resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("app must stay within the repository")
    safe_argv(config.get("build"), "build")
    if type(config.get("setup_uv", False)) is not bool:
        raise ValueError("setup_uv must be boolean")
    if not isinstance(config.get("tag_prefix", "v"), str) or not re.fullmatch(r"[A-Za-z0-9._-]*", config.get("tag_prefix", "v")):
        raise ValueError("tag_prefix must contain only letters, digits, dots, hyphens or underscores")
    sparkle = config.get("sparkle")
    if sparkle is not None:
        if not isinstance(sparkle, dict) or set(sparkle) != {"appcast", "generate"}:
            raise ValueError("sparkle must contain only appcast and generate")
        safe_asset(sparkle.get("appcast"), "sparkle.appcast")
        if not sparkle["appcast"].endswith(".xml"):
            raise ValueError("sparkle.appcast must end with .xml")
        safe_argv(sparkle.get("generate"), "sparkle.generate")
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


def appcast(config, output, release_tag, repository):
    sparkle = config.get("sparkle")
    if sparkle is None:
        raise ValueError("sparkle appcast generation is not configured")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an owner/name pair")
    if not release_tag or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release_tag):
        raise ValueError("release tag is required for appcast generation")
    archive = output / (config["artifact"] + ".zip")
    destination = output / sparkle["appcast"]
    if not archive.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError("Appcast requires a new output and the verified release archive")
    prefix = f"https://github.com/{repository}/releases/download/{release_tag}/"
    command = sparkle["generate"] + [
        "--archive", str(archive.resolve()),
        "--output", str(destination.resolve()),
        "--download-url-prefix", prefix,
    ]
    subprocess.run(command, check=True, timeout=300)
    if destination.is_symlink() or not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError("Appcast generator did not create a nonempty regular file")
    print(f"Created signed appcast {destination.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("metadata", "package", "appcast"))
    parser.add_argument("--config", default=".github/macos-app.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--repository", default="")
    args = parser.parse_args()
    config = configuration(args.config)
    if args.operation == "metadata":
        print("artifact=" + config["artifact"])
        print("setup_uv=" + str(config.get("setup_uv", False)).lower())
        print("appcast=" + (config.get("sparkle") or {}).get("appcast", ""))
    elif args.operation == "package":
        if args.output is None:
            parser.error("package requires --output")
        package(config, args.output, args.release_tag)
    else:
        if args.output is None:
            parser.error("appcast requires --output")
        appcast(config, args.output, args.release_tag, args.repository)


if __name__ == "__main__":
    main()

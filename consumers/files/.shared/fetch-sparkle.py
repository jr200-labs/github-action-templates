"""Fetch and verify the pinned Sparkle binary distribution."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def fetch(destination):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError("destination already exists")

    release = json.loads((Path(__file__).resolve().parent / "sparkle.json").read_text())
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "Sparkle.tar.xz"
        with urllib.request.urlopen(release["url"], timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != release["sha256"]:
            raise RuntimeError(f"Sparkle archive digest mismatch: {digest}")
        destination.mkdir(parents=True)
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(destination, filter="data")
    framework = destination / "Sparkle.framework"
    if not framework.is_dir() or not (destination / "bin/generate_appcast").is_file():
        raise RuntimeError("Sparkle distribution is incomplete")
    print(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    fetch(args.destination)


if __name__ == "__main__":
    main()

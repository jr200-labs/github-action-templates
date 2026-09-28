"""Generate a signed Sparkle appcast for one verified release archive."""

import argparse
import base64
import binascii
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def sparkle_fetcher():
    path = Path(__file__).resolve().parent / "fetch-sparkle.py"
    spec = importlib.util.spec_from_file_location("fetch_sparkle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fetch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--download-url-prefix", required=True)
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not archive.is_file() or archive.suffix != ".zip":
        parser.error("--archive must be an existing ZIP")
    if output.exists() or output.suffix != ".xml" or output.parent != archive.parent:
        parser.error("--output must be a new XML beside the archive")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[A-Za-z0-9._-]+/",
        args.download_url_prefix,
    ):
        parser.error("--download-url-prefix must identify one GitHub release")
    private_key = os.environ.get("SPARKLE_EDDSA_PRIVATE_KEY", "")
    if not private_key or any(character.isspace() for character in private_key):
        parser.error("SPARKLE_EDDSA_PRIVATE_KEY must contain the signing key")
    try:
        decoded_private_key = base64.b64decode(private_key, validate=True)
    except (binascii.Error, ValueError):
        parser.error("SPARKLE_EDDSA_PRIVATE_KEY must be valid base64")
    if len(decoded_private_key) != 96:
        parser.error("SPARKLE_EDDSA_PRIVATE_KEY must decode to a 96-byte Sparkle EdDSA key")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        distribution = temporary / "sparkle"
        feed = temporary / "feed"
        sparkle_fetcher()(distribution)
        feed.mkdir()
        shutil.copy2(archive, feed / archive.name)
        subprocess.run(
            [
                str(distribution / "bin/generate_appcast"),
                "--ed-key-file",
                "-",
                "--download-url-prefix",
                args.download_url_prefix,
                "--maximum-versions",
                "1",
                "--maximum-deltas",
                "0",
                str(feed),
            ],
            input=private_key + "\n",
            text=True,
            check=True,
            timeout=120,
        )
        generated = feed / "appcast.xml"
        if not generated.is_file() or generated.stat().st_size == 0:
            raise RuntimeError("Sparkle did not create the appcast")
        shutil.move(generated, output)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Sparkle did not create the appcast")


if __name__ == "__main__":
    main()

"""Publish an exact macOS draft release using only the Python standard library."""

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_VERSION = "2022-11-28"


def safe_repository(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("repository must be an owner/name pair")
    return value


def safe_asset(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError(f"{field} must be a safe 1–80 character asset name")
    return value


def api_request(repository, token, path, method="GET", body=None, upload=False):
    safe_repository(repository)
    if not isinstance(token, str) or len(token) < 20 or any(character.isspace() for character in token):
        raise ValueError("GitHub token is missing or invalid")
    prefix = f"/repos/{repository}/releases"
    release_path = re.escape(prefix) + r"/[1-9][0-9]*"
    valid_path = re.fullmatch(release_path, path or "") or re.fullmatch(
        release_path + r"/assets\?name=[A-Za-z0-9._%~-]+", path or ""
    ) or re.fullmatch(re.escape(prefix) + r"\?per_page=100&page=[1-9][0-9]{0,2}", path or "")
    if not valid_path or method not in ("GET", "POST", "PATCH"):
        raise ValueError("GitHub release API path is outside the expected repository")
    if upload != (method == "POST" and "/assets?name=" in path):
        raise ValueError("GitHub upload requests must use the fixed release asset endpoint")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "github-action-templates-macos-release",
        "X-GitHub-Api-Version": API_VERSION,
    }
    data = body
    if isinstance(body, bytes):
        headers["Content-Type"] = "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    base = "https://uploads.github.com" if upload else "https://api.github.com"
    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read()
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"GitHub release API request failed ({getattr(error, 'code', 'network error')})") from error
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub release API returned invalid JSON") from error


def checked_release(release, repository, release_tag, release_id):
    if not isinstance(release, dict) or release.get("id") != release_id or release.get("tag_name") != release_tag:
        raise ValueError("GitHub returned a different release identity")
    if release.get("draft") is not True:
        raise ValueError(f"{release_tag} must remain a draft until its verified assets are attached")
    expected = f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets"
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str) or upload_url.split("{", 1)[0] != expected:
        raise ValueError("The draft release returned an unexpected asset upload URL")
    assets = release.get("assets", [])
    if not isinstance(assets, list) or any(
        not isinstance(asset, dict) or not isinstance(asset.get("name"), str) for asset in assets
    ):
        raise ValueError("The draft release returned invalid assets")
    return release


def resolve_release(repository, release_tag, release_id, token):
    safe_repository(repository)
    if not isinstance(release_tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release_tag):
        raise ValueError("release tag is invalid")
    if release_id:
        if not isinstance(release_id, str) or not re.fullmatch(r"[1-9][0-9]*", release_id):
            raise ValueError("release ID must be a positive integer")
        numeric_id = int(release_id)
        checked_release(api_request(repository, token, f"/repos/{repository}/releases/{numeric_id}"),
                        repository, release_tag, numeric_id)
        return numeric_id
    matches = []
    for page in range(1, 101):
        releases = api_request(repository, token, f"/repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("GitHub returned an invalid release list")
        matches.extend(release for release in releases if isinstance(release, dict) and release.get("tag_name") == release_tag)
        if len(releases) < 100:
            break
    else:
        raise ValueError("Release lookup exceeded the bounded pagination limit")
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one release named {release_tag}")
    numeric_id = matches[0].get("id")
    if type(numeric_id) is not int or numeric_id < 1:
        raise ValueError("GitHub returned an invalid release ID")
    checked_release(matches[0], repository, release_tag, numeric_id)
    return numeric_id


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def publish_release(repository, release_tag, release_id, token, output, artifact, appcast=""):
    safe_repository(repository)
    safe_asset(artifact, "artifact")
    if type(release_id) is not int or release_id < 1:
        raise ValueError("release ID must be a positive integer")
    names = [artifact + ".zip", artifact + ".zip.sha256"]
    if appcast:
        safe_asset(appcast, "appcast")
        if not appcast.endswith(".xml"):
            raise ValueError("appcast must end with .xml")
        names.append(appcast)
    files = {name: output / name for name in names}
    if any(path.is_symlink() or not path.is_file() or path.stat().st_size == 0 for path in files.values()):
        raise ValueError("Every release asset must be a nonempty regular file")
    archive_digest = file_digest(files[names[0]]).removeprefix("sha256:")
    if files[names[1]].read_text() != f"{archive_digest}  {names[0]}\n":
        raise ValueError("Archive checksum does not match the verified local file")
    digests = {name: file_digest(path) for name, path in files.items()}
    release_path = f"/repos/{repository}/releases/{release_id}"

    def load():
        return checked_release(api_request(repository, token, release_path), repository, release_tag, release_id)

    release = load()
    unexpected = sorted({asset["name"] for asset in release["assets"] if asset["name"] not in names})
    if unexpected:
        raise ValueError(f"Draft release contains assets outside the verified publication set: {unexpected}")
    for name in names:
        release = load()
        matches = [asset for asset in release["assets"] if asset["name"] == name]
        if len(matches) == 1:
            if matches[0].get("state") == "uploaded" and matches[0].get("digest") == digests[name]:
                continue
            raise ValueError(f"{name} already exists but does not match the verified local file")
        if matches:
            raise ValueError(f"{name} appears more than once on the draft release")
        upload_path = f"/repos/{repository}/releases/{release_id}/assets?name={quote(name, safe='')}"
        api_request(repository, token, upload_path, method="POST", body=files[name].read_bytes(), upload=True)

    release = load()
    uploaded = [asset for asset in release["assets"] if asset.get("state") == "uploaded"]
    if sorted(asset["name"] for asset in uploaded) != sorted(names) or any(
        len([asset for asset in uploaded if asset["name"] == name and asset.get("digest") == digests[name]]) != 1
        for name in names
    ):
        raise ValueError("Draft release assets do not exactly match the verified publication set")
    published = api_request(repository, token, release_path, method="PATCH",
                            body={"draft": False, "make_latest": "true"})
    if (not isinstance(published, dict) or published.get("id") != release_id or
            published.get("tag_name") != release_tag or published.get("draft") is not False):
        raise RuntimeError("GitHub did not confirm release publication")


def main():
    mode = os.environ.get("RELEASE_MODE")
    repository = os.environ.get("RELEASE_REPOSITORY", "")
    token = os.environ.get("RELEASE_TOKEN", "")
    release_tag = os.environ.get("RELEASE_TAG", "")
    release_id = os.environ.get("RELEASE_ID", "")
    if mode == "resolve":
        resolved = resolve_release(repository, release_tag, release_id, token)
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise ValueError("GITHUB_OUTPUT is required")
        with Path(output).open("a") as stream:
            stream.write(f"release-id={resolved}\n")
    elif mode == "publish":
        if not re.fullmatch(r"[1-9][0-9]*", release_id):
            raise ValueError("release ID must be a positive integer")
        publish_release(repository, release_tag, int(release_id), token,
                        Path(os.environ.get("RELEASE_OUTPUT", "")),
                        os.environ.get("RELEASE_ARTIFACT", ""), os.environ.get("RELEASE_APPCAST", ""))
    else:
        raise ValueError("mode must be resolve or publish")


if __name__ == "__main__":
    main()

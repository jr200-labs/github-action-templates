#!/usr/bin/env python3
"""Delete assets from superseded releases after 14 days; preserve releases and tags."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

RETENTION_DAYS = 14


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def release_line(release):
    # Component releases (e.g. shared-v1.2.3) each keep their own current assets.
    match = re.fullmatch(r'(.*?)(?:v)?\d+\.\d+\.\d+(?:[-+].*)?', release['tag_name'])
    return match.group(1) if match else release['tag_name']


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('invalid repository')
        self.prefix = 'https://api.github.com/repos/' + repository
        self.token = token

    def request(self, path, method='GET'):
        request = urllib.request.Request(self.prefix + path, method=method, headers={
            'Authorization': 'Bearer ' + self.token,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise RuntimeError(f'GitHub {method} {path} failed with status {error.code}') from None

    def inventory(self, path):
        result = []
        for page in range(1, 1001):
            data = self.request(f'{path}?per_page=100&page={page}')
            if not isinstance(data, list):
                raise RuntimeError('release inventory unavailable; deletion stopped')
            result.extend(data)
            if len(data) < 100:
                return result
        raise RuntimeError('release inventory incomplete; deletion stopped')


def protected_releases(releases, latest, protected_tags):
    protected = {r['id'] for r in releases if r.get('draft') or r.get('immutable') or r['tag_name'] in protected_tags}
    if latest:
        protected.add(latest['id'])
    current = {}
    for release in releases:
        if release.get('draft') or not release.get('published_at'):
            continue
        key = (release_line(release), bool(release.get('prerelease')))
        if key not in current or timestamp(release['published_at']) > timestamp(current[key]['published_at']):
            current[key] = release
    protected.update(r['id'] for r in current.values())
    return protected


def prune(api, now, protected_tags=(), apply=False):
    cutoff = now - timedelta(days=RETENTION_DAYS)
    releases = api.inventory('/releases')
    latest = api.request('/releases/latest')
    protected = protected_releases(releases, latest, protected_tags)
    candidates = []
    for release in releases:
        if release['id'] in protected or not release.get('published_at') or timestamp(release['published_at']) > cutoff:
            continue
        # Embedded release asset lists may be truncated; paginate every eligible release.
        for asset in api.inventory(f"/releases/{release['id']}/assets"):
            if asset.get('state') == 'uploaded' and timestamp(asset['created_at']) <= cutoff and timestamp(asset['updated_at']) <= cutoff:
                candidates.append((release, asset))
    deleted, reclaimed = [], 0
    for release, asset in candidates:
        # Refresh current releases before every deletion. A rollback can promote an old release.
        fresh = api.inventory('/releases')
        current_ids = protected_releases(fresh, api.request('/releases/latest'), protected_tags)
        actual_release = next((r for r in fresh if r['id'] == release['id']), None)
        if actual_release is None or release['id'] in current_ids:
            continue
        if any(actual_release.get(key) != release.get(key) for key in ['tag_name', 'published_at', 'draft', 'prerelease']):
            raise RuntimeError('release changed since inventory; deletion stopped')
        path = f"/releases/assets/{asset['id']}"
        actual = api.request(path)
        if actual is None:
            continue
        if any(actual.get(key) != asset.get(key) for key in ['id', 'name', 'size', 'created_at', 'updated_at', 'state']):
            raise RuntimeError('release asset changed since inventory; deletion stopped')
        if apply:
            api.request(path, 'DELETE')
            if api.request(path) is not None:
                raise RuntimeError('release asset deletion did not converge')
            deleted.append(asset['id'])
            reclaimed += asset['size']
    return {'apply': apply, 'retention_days': RETENTION_DAYS, 'candidates': len(candidates), 'deleted_asset_ids': deleted, 'reclaimed_bytes': reclaimed, 'immutable_release_ids': [r['id'] for r in releases if r.get('immutable')]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    policy_path = Path('.github/release-asset-retention.json')
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {'protected_tags': []}
    if set(policy) != {'protected_tags'} or not isinstance(policy['protected_tags'], list) or any(not isinstance(tag, str) or not tag for tag in policy['protected_tags']):
        raise ValueError('release-asset-retention.json must contain a protected_tags string array')
    result = prune(GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN']), datetime.now(timezone.utc), policy['protected_tags'], args.apply)
    print(json.dumps(result, sort_keys=True))
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as output:
            output.write(f"Release assets: {len(result['deleted_asset_ids'])} superseded assets deleted after 14 days; {result['reclaimed_bytes']} bytes reclaimed; {len(result['immutable_release_ids'])} immutable releases preserved.\n")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Read-only Actions artifact storage totals across an organization."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('artifact_cleanup', Path(__file__).resolve().parents[1] / 'consumers/files/.shared/cleanup-actions-artifacts.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def repositories(owner, token):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', owner):
        raise ValueError('invalid organization name')
    api = cleanup.GitHub(owner + '/.github', token)
    api.prefix = 'https://api.github.com/orgs/' + owner
    result = set()
    for page in range(1, 1001):
        items = api.request(f'/repos?type=all&per_page=100&page={page}')
        if not isinstance(items, list):
            raise RuntimeError('organization repository inventory unavailable')
        for item in items:
            name = item['full_name']
            if name.split('/')[0].lower() != owner.lower():
                raise RuntimeError('repository inventory returned a different owner')
            result.add(name)
        if len(items) < 100:
            return sorted(result)
    raise RuntimeError('organization repository inventory exceeded page limit')


def report(targets, token, now):
    rows, errors = [], []
    cutoff = now - timedelta(days=5)
    for index, repository in enumerate(targets, 1):
        cleanup.log(f'Storage report {index}/{len(targets)}: {repository}')
        try:
            artifacts = {item['id']: item for item in cleanup.GitHub(repository, token).artifacts() if not item.get('expired')}
            size = sum(item['size_in_bytes'] for item in artifacts.values())
            old_size = sum(item['size_in_bytes'] for item in artifacts.values() if datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')) <= cutoff)
            rows.append({'repository': repository, 'artifact_count': len(artifacts), 'bytes': size, 'older_than_five_days_bytes': old_size})
            cleanup.log(f'{repository}: {len(artifacts)} live artifacts; {size} bytes; {old_size} bytes older than five days')
        except Exception as error:
            errors.append({'repository': repository, 'error': str(error)})
            cleanup.log(f'{repository}: inventory failed; excluded from verified totals')
    rows.sort(key=lambda row: (-row['bytes'], row['repository']))
    return {'observed_at': now.isoformat(), 'repositories': rows, 'total_verified_bytes': sum(row['bytes'] for row in rows), 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', default='whengas', help='GitHub organization (default: whengas)')
    parser.add_argument('--json', action='store_true', help='Print JSON instead of the table')
    args = parser.parse_args()
    token = cleanup.resolve_token()
    result = report(repositories(args.owner, token), token, datetime.now(timezone.utc))
    if args.json:
        print(json.dumps(result, indent=2), flush=True)
    else:
        print(f"{'Repository':45} {'Artifacts':>9} {'Live MiB':>12} {'Older than 5d MiB':>18}")
        for row in result['repositories']:
            print(f"{row['repository']:45} {row['artifact_count']:9d} {row['bytes'] / 1048576:12.2f} {row['older_than_five_days_bytes'] / 1048576:18.2f}")
        print(f"Total verified: {result['total_verified_bytes'] / 1048576:.2f} MiB")
        for error in result['errors']:
            print(f"UNREADABLE {error['repository']}: {error['error']}", file=sys.stderr, flush=True)
    return bool(result['errors'])


if __name__ == '__main__':
    sys.exit(main())

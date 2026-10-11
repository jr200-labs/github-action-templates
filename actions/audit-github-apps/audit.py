#!/usr/bin/env python3
"""Read-only organization App audit; configuration belongs to the caller."""

import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_inventory(path, organization):
    config = json.loads(Path(path).read_text())
    if config.get('version') != 1 or config.get('organization') != organization:
        raise ValueError('inventory version must be 1 and organization must match the caller')
    apps = config.get('apps')
    if not isinstance(apps, list) or not apps:
        raise ValueError('inventory must contain a nonempty apps list')
    slugs = set()
    for app in apps:
        slug = app.get('slug', '')
        if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', slug) or slug in slugs:
            raise ValueError('app slugs must be valid and unique')
        slugs.add(slug)
        if app.get('repository_selection') not in ('all', 'selected'):
            raise ValueError(f'{slug}: repository_selection must be all or selected')
        if not isinstance(app.get('app_id'), int) or app['app_id'] <= 0:
            raise ValueError(f'{slug}: app_id must be a positive integer')
        if 'permissions' in app:
            permissions = app['permissions']
            if not isinstance(permissions, dict) or any(value not in ('read', 'write', 'admin') for value in permissions.values()):
                raise ValueError(f'{slug}: invalid permissions')
            events = app.get('events')
            if not isinstance(events, list) or any(not isinstance(event, str) for event in events):
                raise ValueError(f'{slug}: events must be a string list')
        elif 'events' in app:
            raise ValueError(f'{slug}: events require permissions')
        if app.get('device_flow_required') and not app.get('client_id'):
            raise ValueError(f'{slug}: device flow requires client_id')
    return apps


def request_json(url, token=None, data=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'github-app-inventory-audit'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
        headers['X-GitHub-Api-Version'] = '2022-11-28'
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request(url, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Never echo upstream bodies, issued device codes or authentication data.
        if token:
            advice = ' Check the audit App and installation grant organization Administration read.' if exc.code in (401, 403) else ''
            raise RuntimeError(f'GitHub installation lookup failed (HTTP {exc.code}).{advice}') from None
        try:
            return json.loads(exc.read())
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError(f'GitHub device flow lookup failed (HTTP {exc.code}).') from None


def installations(organization, token):
    result = {}
    page = 1
    while True:
        data = request_json(f'https://api.github.com/orgs/{urllib.parse.quote(organization, safe="")}/installations?per_page=100&page={page}', token)
        batch = data['installations']
        for app in batch:
            result[app['app_slug']] = app
        if len(batch) < 100:
            return result
        page += 1


def differences(expected, current):
    if current is None:
        return ['missing installation']
    problems = []
    for field in ('app_id', 'repository_selection'):
        if expected[field] != current.get(field):
            problems.append(f'{field}: {current.get(field)} -> {expected[field]}')
    if current.get('suspended_at'):
        problems.append('installation is suspended')
    if 'permissions' in expected:
        wanted, actual = expected['permissions'], current.get('permissions', {})
        for permission in sorted(wanted.keys() | actual.keys()):
            if wanted.get(permission) != actual.get(permission):
                problems.append(f'permission {permission}: {actual.get(permission, "absent")} -> {wanted.get(permission, "absent")}')
        wanted_events, actual_events = set(expected['events']), set(current.get('events', []))
        for event in sorted(wanted_events - actual_events):
            problems.append(f'event add {event}')
        for event in sorted(actual_events - wanted_events):
            problems.append(f'event remove {event}')
    return problems


def device_flow_problems(app):
    if not app.get('device_flow_required'):
        return []
    data = request_json('https://github.com/login/device/code', data=urllib.parse.urlencode({'client_id': app['client_id']}).encode())
    if data.get('error') == 'device_flow_disabled':
        return ['device flow is disabled']
    if data.get('error') or not data.get('device_code') or not data.get('user_code'):
        return ['device flow check failed']
    return []


def audit(apps, live):
    failed = False
    for app in apps:
        problems = differences(app, live.get(app['slug']))
        problems.extend(device_flow_problems(app))
        print(f'{"DRIFT" if problems else "OK"} {app["slug"]}')
        for problem in problems:
            print(f'  - {problem}')
        failed |= bool(problems)
    return int(failed)


def main():
    try:
        path, org, validate_only = sys.argv[1:]
        apps = load_inventory(path, org)
        if validate_only == 'true':
            print(f'Valid inventory: {org}, {len(apps)} apps')
            return 0
        if validate_only != 'false':
            raise ValueError('validate-only must be true or false')
        token = os.environ.get('GH_TOKEN')
        if not token:
            raise ValueError('GH_TOKEN is required: use an installation token with organization Administration read')
        return audit(apps, installations(org, token))
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f'GitHub App audit failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

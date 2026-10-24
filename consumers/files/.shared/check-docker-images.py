#!/usr/bin/env python3
"""Build local production images and run optional repository-owned assertions."""
import json
from pathlib import Path
import subprocess
import uuid


def images():
    catalog = Path('.github/images.yaml')
    if catalog.exists():
        data = json.loads(subprocess.check_output(['yq', '-o=json', str(catalog)], text=True))
        entries = data['images']
    else:
        dockerfile = 'Dockerfile' if Path('Dockerfile').is_file() else 'docker/Dockerfile'
        entries = [{'dockerfile': dockerfile}]
    if not isinstance(entries, list) or not entries:
        raise ValueError('images must be a nonempty list')
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('each image must be an object')
        for field, default in [('dockerfile', 'Dockerfile'), ('context', '.')]:
            value = entry.get(field, default)
            if not isinstance(value, str) or not value or value.startswith('-'):
                raise ValueError(f'{field} must be a nonempty path')
        contexts = entry.get('build_contexts', '')
        if not isinstance(contexts, str):
            raise ValueError('build_contexts must be a newline-delimited string')
        smoke = entry.get('smoke-test')
        if smoke is not None and (
            not isinstance(smoke, list) or not smoke
            or any(not isinstance(arg, str) or not arg for arg in smoke)
        ):
            raise ValueError('smoke-test must be a nonempty argv array')
    return entries


def main():
    for entry in images():
        tag = f'gat-ci:{uuid.uuid4().hex}'
        command = ['docker', 'build', '--tag', tag, '--file', entry.get('dockerfile', 'Dockerfile')]
        for context in entry.get('build_contexts', '').splitlines():
            if context.strip():
                command.extend(['--build-context', context.strip()])
        command.append(entry.get('context', '.'))
        try:
            subprocess.run(command, check=True)
            if entry.get('smoke-test'):
                subprocess.run([*entry['smoke-test'], tag], check=True)
        finally:
            subprocess.run(['docker', 'image', 'rm', tag], check=False)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Build local production images and run optional repository-owned assertions."""
import json
import os
from pathlib import Path
import subprocess
import time
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


def build_command(entry, tag, index):
    dockerfile = entry.get('dockerfile', 'Dockerfile')
    builder = os.environ.get('GAT_DOCKER_BUILDER')
    if not builder:
        return ['docker', 'build', '--tag', tag, '--file', dockerfile]
    cache = Path(os.environ['GAT_DOCKER_CACHE_DIR'])
    cache_import = cache / 'import' / str(index)
    cache_export = cache / 'export' / str(index)
    command = ['docker', 'buildx', 'build', '--builder', builder, '--load', '--progress=plain',
               '--tag', tag, '--file', dockerfile]
    if (cache_import / 'index.json').is_file():
        command.extend(['--cache-from', f'type=local,src={cache_import}'])
    if os.environ.get('GAT_DOCKER_CACHE_EXPORT') == 'true':
        cache_export.mkdir(parents=True, exist_ok=True)
        command.extend(['--cache-to', f'type=local,dest={cache_export},mode=max,ignore-error=true'])
    return command


def main():
    for index, entry in enumerate(images()):
        tag = f'gat-ci:{uuid.uuid4().hex}'
        command = build_command(entry, tag, index)
        for context in entry.get('build_contexts', '').splitlines():
            if context.strip():
                command.extend(['--build-context', context.strip()])
        command.append(entry.get('context', '.'))
        try:
            started = time.monotonic()
            subprocess.run(command, check=True)
            print(f"Image {index + 1} build and load: {time.monotonic() - started:.1f}s", flush=True)
            if entry.get('smoke-test'):
                started = time.monotonic()
                subprocess.run([*entry['smoke-test'], tag], check=True)
                print(f"Image {index + 1} smoke test: {time.monotonic() - started:.1f}s", flush=True)
        finally:
            subprocess.run(['docker', 'image', 'rm', tag], check=False)


if __name__ == '__main__':
    main()

"""Bind verification and packaging jobs to the same source and build inputs."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess


def digest(path):
    with path.open('rb') as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=60).strip()


def identity(config, tag, environment):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', environment.get('GITHUB_REPOSITORY', '')):
        raise ValueError('Repository identity is required')
    if tag and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Exact release tag is required')
    declaration = Path('.github/macos-verification-inputs.json')
    if config.get('verify') and not declaration.exists():
        raise ValueError('Separate verification requires declared toolchain inputs')
    if declaration.is_symlink() or declaration.exists() and declaration.stat().st_size > 65536:
        raise ValueError('Invalid verification input declaration')
    inputs = json.loads(declaration.read_text()) if declaration.exists() else None
    if inputs is not None and (not isinstance(inputs, list) or not inputs or any(not isinstance(arg, str) or not arg or '\0' in arg for arg in inputs)):
        raise ValueError('Verification inputs must be an argv array')
    source = command(['git', 'rev-parse', 'HEAD'])
    if tag and source != command(['git', 'rev-parse', f'refs/tags/{tag}^{{commit}}']):
        raise ValueError('Checkout must match the exact release tag')
    if command(['git', 'status', '--porcelain', '--untracked-files=normal']):
        raise ValueError('Release reuse requires a clean source tree')
    # The repository declares actual runtime, compiler, SDK, simulator and signing
    # inputs; locked dependencies and the generator itself are bound by source.
    declared = json.loads(command(inputs)) if inputs is not None else {"declared": False}
    if not isinstance(declared, dict) or not declared:
        raise ValueError('Verification input command must return a nonempty JSON object')
    if command(['git', 'status', '--porcelain', '--untracked-files=normal']):
        raise ValueError('Verification input declaration changed the source tree')
    value = dict(repository=environment['GITHUB_REPOSITORY'], source=source, tag=tag,
                 config=config, declaration=inputs, inputs=declared, host=[platform.system(), platform.machine(), platform.release()],
                 provider=[digest(Path(__file__)), digest(Path(__file__).with_name('action.yml')),
                           digest(Path(__file__).parents[2] / '.github/workflows/build_macos_app.yaml')])
    return 'macos-verified-v1-' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def package_identity(config):
    declaration = Path('.github/macos-package-inputs.json')
    if not declaration.exists():
        if config.get('verify'):
            raise ValueError('Separate verification requires declared packaging inputs')
        return None
    if declaration.is_symlink() or declaration.stat().st_size > 65536:
        raise ValueError('Invalid packaging input declaration')
    argv = json.loads(declaration.read_text())
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or '\0' in arg for arg in argv):
        raise ValueError('Packaging inputs must be an argv array')
    value = json.loads(command(argv))
    if not isinstance(value, dict) or not value:
        raise ValueError('Packaging inputs must be a nonempty JSON object')
    if command(['git', 'status', '--porcelain', '--untracked-files=normal']):
        raise ValueError('Packaging probe changed the source tree')
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validation(config, directory, key, tag, environment, *, save=False):
    # Native/simulator probes run only in verification. Packaging checks its
    # own compiler/runtime declaration and the upstream receipt, never a device.
    if save and identity(config, tag, environment) != key:
        raise ValueError('Verification inputs changed during verification')
    source = command(['git', 'rev-parse', 'HEAD'])
    if command(['git', 'status', '--porcelain', '--untracked-files=normal']):
        raise ValueError('Verification handoff requires a clean source tree')
    spent = float(environment.get('REUSE_SPENT', '0'))
    if not math.isfinite(spent) or not 0 <= spent < 1200:
        raise ValueError('Invalid verification execution time')
    expected = dict(schema=1, key=key, repository=environment['GITHUB_REPOSITORY'], source=source,
                    tag=tag, verification_run=environment['GITHUB_RUN_ID'], execution_seconds=spent,
                    package_inputs=package_identity(config))
    if save:
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'validation.json').write_text(json.dumps(expected))
    else:
        path = directory / 'validation.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise ValueError('Missing bounded verification receipt')
        if json.loads(path.read_text()) != expected:
            raise ValueError('Verification receipt does not match this source/run/build inputs')


def main():
    config = json.loads(Path(os.environ['REUSE_CONFIG']).read_text())
    tag = os.environ['REUSE_TAG']
    mode = os.environ['REUSE_MODE']
    root = Path(os.environ['RUNNER_TEMP'])
    result = {}
    if mode == 'stage-identity':
        result = dict(key=identity(config, tag, os.environ), source=command(['git', 'rev-parse', 'HEAD']))
    elif mode in ('verify-save', 'verify-check'):
        validation(config, root / 'macos-verification', os.environ['REUSE_KEY'], tag, os.environ, save=mode == 'verify-save')
    else:
        raise ValueError('Unknown verification handoff operation')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        for name, value in result.items():
            stream.write(f'{name}={value}\n')


if __name__ == '__main__':
    main()

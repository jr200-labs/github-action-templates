"""Promote immutable release bytes only after successful repository verification.

The Actions cache supplies ref isolation and immutable storage. No restore prefix,
PR cache writer, mutable host cache or saved generic test verdict is accepted.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time


MAX_AGE = 7 * 24 * 3600


def digest(path):
    with path.open('rb') as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=60).strip()


def identity(config, tag, environment, *, stage=False):
    if not stage and environment.get('GITHUB_EVENT_NAME') not in ('repository_dispatch', 'workflow_dispatch'):
        raise ValueError('Verified release reuse requires a trusted release event')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', environment.get('GITHUB_REPOSITORY', '')):
        raise ValueError('Repository identity is required')
    if (tag or not stage) and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Exact release tag is required')
    declaration = Path('.github/macos-verification-inputs.json')
    if not declaration.exists() and not stage:
        return None
    if stage and config.get('verify') and not declaration.exists():
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
                 epoch=int(time.time() // MAX_AGE),
                 provider=[digest(Path(__file__)), digest(Path(__file__).with_name('action.yml')),
                           digest(Path(__file__).parents[2] / '.github/workflows/build_macos_app.yaml')])
    return 'macos-verified-v1-' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def names(config):
    artifact = config.get('artifact', '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', artifact):
        raise ValueError('Invalid release artifact basename')
    return artifact + '.zip', artifact + '.zip.sha256'


def checked(config, directory, key, tag):
    if not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', key):
        raise ValueError('Invalid verification key')
    expected = set(names(config)) | {'verification.json'}
    if directory.is_symlink() or set(p.name for p in directory.iterdir()) != expected:
        raise ValueError('Unexpected verified archive files')
    if any((directory / name).is_symlink() or not (directory / name).is_file() for name in expected):
        raise ValueError('Verified archive files must be regular')
    receipt_path = directory / 'verification.json'
    if receipt_path.stat().st_size > 4096:
        raise ValueError('Invalid verification receipt')
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('key') != key or receipt.get('tag') != tag or receipt.get('schema') != 1
            or not re.fullmatch(r'[1-9][0-9]*', str(receipt.get('verification_run', '')))
            or type(receipt.get('verified_at')) not in (int, float)
            or not 0 <= time.time() - receipt['verified_at'] < MAX_AGE):
        raise ValueError('Verification receipt is incompatible or expired')
    archive, checksum = (directory / name for name in names(config))
    sha = digest(archive)
    if not archive.stat().st_size or receipt.get('sha256') != sha or checksum.read_text() != f'{sha}  {archive.name}\n':
        raise ValueError('Verified archive checksum mismatch')
    return receipt


def save(config, output, cache, key, tag, verification_run):
    # Only called after the repository build/tests, version and codesign checks
    # and archive creation succeed. Publish failures can safely reuse these bytes.
    if not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', key):
        raise ValueError('Invalid verification key')
    if not re.fullmatch(r'[1-9][0-9]*', verification_run):
        raise ValueError('Successful verification run identity is required')
    archive, checksum = (output / name for name in names(config))
    if any(path.is_symlink() or not path.is_file() for path in (archive, checksum)):
        raise ValueError('Missing verified release archive')
    sha = digest(archive)
    if checksum.read_text() != f'{sha}  {archive.name}\n':
        raise ValueError('Verified archive checksum mismatch')
    if cache.exists() or cache.is_symlink():
        raise ValueError('Verified archive cache must be new')
    with tempfile.TemporaryDirectory(dir=cache.parent) as temporary:
        pending = Path(temporary) / 'prepared'
        pending.mkdir()
        for path in (archive, checksum):
            shutil.copy2(path, pending / path.name)
        (pending / 'verification.json').write_text(json.dumps(dict(schema=1, key=key, tag=tag,
                                                  sha256=sha, verified_at=time.time(),
                                                  verification_run=verification_run)))
        checked(config, pending, key, tag)
        pending.rename(cache)


def restore(config, output, cache, key, tag):
    if output.exists() or output.is_symlink():
        raise ValueError('Release output must be new')
    if not cache.exists():
        return False
    # A corrupt/expired exact cache entry fails closed. It must never silently
    # substitute bytes or rerun acceptance and overwrite an immutable cache key.
    receipt = checked(config, cache, key, tag)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        pending = Path(temporary) / 'release'
        pending.mkdir()
        for name in names(config):
            shutil.copy2(cache / name, pending / name)
        if digest(pending / names(config)[0]) != receipt['sha256']:
            raise ValueError('Verified archive changed during promotion')
        pending.rename(output)
    return True


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
    if save and identity(config, tag, environment, stage=True) != key:
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


def bundle(config, directory, key, tag, source, environment, *, save=False, appcast=''):
    """Seal/check final bytes without native tools, probing, rebuilding or tests."""
    expected = dict(schema=1, key=key, repository=environment['GITHUB_REPOSITORY'], source=source,
                    tag=tag, verification_run=environment['GITHUB_RUN_ID'])
    if not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', key) or not re.fullmatch(r'[a-f0-9]{40}', source):
        raise ValueError('Invalid publication identity')
    if command(['git', 'rev-parse', 'HEAD']) != source:
        raise ValueError('Publication checkout differs from verified source')
    files = list(names(config))
    if appcast:
        if appcast != config.get('sparkle', {}).get('appcast') or Path(appcast).name != appcast:
            raise ValueError('Unexpected appcast identity')
        files.append(appcast)
    receipt = directory / 'publication.json'
    if directory.is_symlink() or set(path.name for path in directory.iterdir()) != set(files) | (set() if save else {'publication.json'}):
        raise ValueError('Unexpected publication files')
    for name in files:
        path = directory / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError('Publication requires regular nonempty files')
    sha = digest(directory / files[0])
    if (directory / files[1]).read_text() != f'{sha}  {files[0]}\n':
        raise ValueError('Publication archive checksum mismatch')
    expected['files'] = {name: digest(directory / name) for name in files}
    if save:
        receipt.write_text(json.dumps(expected))
    elif (receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 4096
          or json.loads(receipt.read_text()) != expected):
        raise ValueError('Publication receipt does not match verified bytes/source/run')


def main():
    config = json.loads(Path(os.environ['REUSE_CONFIG']).read_text())
    tag = os.environ['REUSE_TAG']
    mode = os.environ['REUSE_MODE']
    root = Path(os.environ['RUNNER_TEMP'])
    cache, output = root / 'macos-verified-release', root / 'macos-release'
    result = {}
    if mode == 'stage-identity':
        result = dict(key=identity(config, tag, os.environ, stage=True), source=command(['git', 'rev-parse', 'HEAD']), enabled=str(Path('.github/macos-verification-inputs.json').exists()).lower())
    elif mode in ('verify-save', 'verify-check'):
        validation(config, root / 'macos-verification', os.environ['REUSE_KEY'], tag, os.environ, save=mode == 'verify-save')
    elif mode in ('bundle-save', 'bundle-check'):
        if mode == 'bundle-save':
            validation(config, root / 'macos-verification', os.environ['REUSE_KEY'], tag, os.environ)
        bundle(config, output, os.environ['REUSE_KEY'], tag, os.environ['REUSE_SOURCE'], os.environ, save=mode == 'bundle-save', appcast=os.environ.get('REUSE_APPCAST', ''))
    elif mode == 'identity':
        key = identity(config, tag, os.environ)
        result = dict(key=key or '', enabled=str(key is not None).lower())
    elif mode == 'restore':
        result['reused'] = str(restore(config, output, cache, os.environ['REUSE_KEY'], tag)).lower()
        print('Verified release archive reused=' + result['reused'])
    elif mode == 'save':
        validation(config, root / 'macos-verification', os.environ['REUSE_KEY'], tag, os.environ)
        save(config, output, cache, os.environ['REUSE_KEY'], tag, os.environ['GITHUB_RUN_ID'])
    else:
        raise ValueError('Unknown verified release operation')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        for name, value in result.items():
            stream.write(f'{name}={value}\n')


if __name__ == '__main__':
    main()

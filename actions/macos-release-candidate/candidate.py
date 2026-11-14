"""Promote a successful full-verification artifact; never run builds, tests or native probes."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SHA = r'[0-9a-f]{40}'
REPOSITORY = r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
MAX_AGE = 5 * 24 * 3600
PREPARATION_WAIT = 350 * 60
ACTIVE_RUN_STATUSES = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}


def git(*args):
    return subprocess.check_output(['git', *args], text=True, timeout=30).strip()


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def policy():
    files = ['actions/macos-release-candidate/candidate.py', 'actions/macos-release-candidate/action.yml',
             'actions/macos-verified-build/reuse.py', 'actions/macos-verify/action.yml',
             'consumers/files/.shared/package-macos-app.py',
             '.github/workflows/build_macos_app.yaml', '.github/workflows/publish_macos_app.yaml']
    return hashlib.sha256(json.dumps({name: digest(ROOT / name) for name in files}, sort_keys=True).encode()).hexdigest()


def config_key(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def asset_names(config):
    name = config.get('artifact', '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', name):
        raise ValueError('Invalid release artifact name')
    return [name + '.zip', name + '.zip.sha256']


def files(config, directory, *, extras=()):
    names = asset_names(config)
    expected = set(names) | set(extras)
    if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != expected:
        raise ValueError('Unexpected immutable release files')
    if any((directory / name).is_symlink() or not (directory / name).is_file()
           or not (directory / name).stat().st_size for name in expected):
        raise ValueError('Release files must be regular and nonempty')
    hashes = {name: digest(directory / name) for name in names}
    if (directory / names[1]).read_text() != f'{hashes[names[0]]}  {names[0]}\n':
        raise ValueError('Release checksum mismatch')
    return hashes


def version(config, archive):
    name = Path(config['app']).name + '/Contents/Info.plist'
    with zipfile.ZipFile(archive) as bundle:
        entries = [item for item in bundle.infolist() if item.filename == name]
        if len(entries) != 1 or not 0 < entries[0].file_size <= 1_048_576:
            raise ValueError('Missing bounded app version in release archive')
        value = plistlib.loads(bundle.read(entries[0])).get('CFBundleShortVersionString')
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value):
        raise ValueError('Invalid app version')
    return value


def release_pr(event, repository):
    pr = event.get('pull_request', {})
    return (pr.get('user', {}).get('type') == 'Bot'
            and pr.get('head', {}).get('repo', {}).get('full_name') == repository
            and pr.get('head', {}).get('ref', '').startswith('release-please--branches--'))


def seal(config, directory, environment, receipt, event):
    repository, run = environment['GITHUB_REPOSITORY'], environment['GITHUB_RUN_ID']
    event_name = environment['GITHUB_EVENT_NAME']
    if event_name != 'workflow_dispatch' and not (event_name == 'pull_request' and release_pr(event, repository)):
        raise ValueError('Only full release preparation can produce a candidate')
    source, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    expected_source = event['pull_request']['head']['sha'] if event_name == 'pull_request' else environment['GITHUB_SHA']
    if source != expected_source or not re.fullmatch(SHA, tree) or git('status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Candidate must use the clean workflow source')
    if any(receipt.get(key) != value for key, value in
           dict(repository=repository, source=source, verification_run=run, key=environment['CANDIDATE_KEY']).items()):
        raise ValueError('Candidate requires this source/run verification receipt')
    if (receipt.get('schema') != 1 or not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', str(receipt.get('key', '')))
            or type(receipt.get('execution_seconds')) not in (int, float)
            or not 0 <= receipt['execution_seconds'] < 1200):
        raise ValueError('Candidate verification receipt is invalid')
    if environment.get('CANDIDATE_FULL') != 'true':
        raise ValueError('Focused verification cannot certify a release')
    hashes = files(config, directory)
    value = dict(schema=1, repository=repository, source=source, tree=tree, producer_run=run,
                 policy=policy(), config=config_key(config), verification_key=receipt['key'],
                 version=version(config, directory / asset_names(config)[0]), files=hashes)
    (directory / 'candidate.json').write_text(json.dumps(value))
    return dict(name=f'macos-release-candidate-{tree}-{run}-{environment.get("GITHUB_RUN_ATTEMPT", "1")}')


class API:
    def __init__(self, repository, token):
        if not re.fullmatch(REPOSITORY, repository) or not isinstance(token, str) or len(token) < 20 or any(c.isspace() for c in token):
            raise ValueError('Invalid repository')
        self.prefix, self.token = '/repos/' + repository, token

    def __call__(self, path):
        allowed = (r'/actions/artifacts(?:\?per_page=100&page=(?:[1-9]|10)|/[1-9][0-9]*)',
                   r'/actions/workflows/macos-app\.yaml/runs\?per_page=10&page=(?:[1-9][0-9]?|100)&event=(?:pull_request|workflow_dispatch)&status=(?:queued|in_progress|waiting|pending|requested)',
                   r'/actions/runs/[1-9][0-9]*/artifacts\?per_page=100&page=(?:[1-9]|10)',
                   r'/actions/runs/[1-9][0-9]*(?:/jobs\?per_page=100&filter=latest|/attempts/[1-9][0-9]*/jobs\?per_page=100)?',
                   r'/commits/' + SHA + r'/pulls\?per_page=100', r'/git/commits/' + SHA)
        if not any(re.fullmatch(pattern, path) for pattern in allowed):
            raise ValueError('Artifact metadata request outside repository scope')
        request = Request('https://api.github.com' + self.prefix + path, method='GET', headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'macos-verified-candidate'})
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(1_048_577)
        except (HTTPError, URLError) as error:
            raise RuntimeError(f'Candidate metadata request failed ({getattr(error, "code", "network")})') from error
        if len(raw) > 1_048_576:
            raise ValueError('Oversized candidate metadata')
        return json.loads(raw)


def preparation_source(api, repository, run, tree):
    if (run.get('path') != '.github/workflows/macos-app.yaml'
            or run.get('head_repository', {}).get('full_name') != repository
            or run.get('repository', {}).get('full_name') != repository
            or not any(row.get('path', '').startswith('jr200-labs/github-action-templates/.github/workflows/build_macos_app.yaml@')
                       and re.fullmatch(SHA, row.get('sha', '')) for row in run.get('referenced_workflows', []))):
        raise ValueError('Candidate producer is not a successful repository workflow')
    source = run.get('head_sha', '')
    if not re.fullmatch(SHA, source) or api('/git/commits/' + source)['tree']['sha'] != tree:
        raise ValueError('Candidate source does not match the release tree')
    if run.get('event') == 'pull_request':
        # Run metadata can have an empty pull_requests list after merge. Commit
        # associations remain authoritative for the exact preparation source.
        pulls = api('/commits/' + source + '/pulls?per_page=100')
        matches = [pr for pr in pulls if release_pr({'pull_request': pr}, repository)
                   and pr.get('head', {}).get('sha') == source
                   and pr.get('head', {}).get('ref') == run.get('head_branch')]
        if len(matches) != 1:
            raise ValueError('Candidate is not from a release PR')
    elif run.get('event') != 'workflow_dispatch':
        raise ValueError('Untrusted candidate workflow event')
    return source


def producer(api, repository, artifact, tree):
    match = re.fullmatch('macos-release-candidate-' + tree + r'-([1-9][0-9]*)-([1-9][0-9]*)', artifact.get('name', ''))
    if artifact.get('expired') or not match:
        raise ValueError('Candidate artifact is expired or for another tree')
    created = datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00')).timestamp()
    if not 0 <= time.time() - created < MAX_AGE:
        raise ValueError('Candidate artifact is too old')
    run_id = artifact['workflow_run']['id']
    if str(run_id) != match[1]:
        raise ValueError('Candidate name and producer run differ')
    run = api('/actions/runs/' + str(run_id))
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        raise ValueError('Candidate producer is not a successful repository workflow')
    source = preparation_source(api, repository, run, tree)
    jobs = api(f'/actions/runs/{run_id}/jobs?per_page=100&filter=latest')['jobs']
    for stage in ('portable', 'verify', 'package'):
        matches = [job for job in jobs if job.get('name', '').endswith(' / ' + stage)]
        if len(matches) != 1 or matches[0].get('conclusion') != 'success':
            raise ValueError('Candidate has no successful preparation jobs')
    attempt_jobs = api(f'/actions/runs/{run_id}/attempts/{match[2]}/jobs?per_page=100')['jobs']
    packaged = [job for job in attempt_jobs if job.get('name', '').endswith(' / package')]
    if len(packaged) != 1 or packaged[0].get('conclusion') != 'success':
        raise ValueError('Candidate packaging attempt was not successful')
    return dict(artifact_id=str(artifact['id']), producer_run=str(run_id), source=source, tree=tree)


def find_candidate(api, repository, tree, run_id=None):
    for page in range(1, 11):
        endpoint = '/actions/artifacts' if run_id is None else f'/actions/runs/{run_id}/artifacts'
        rows = api(f'{endpoint}?per_page=100&page={page}')['artifacts']
        for artifact in sorted(rows, key=lambda row: row['id'], reverse=True):
            if not artifact.get('name', '').startswith('macos-release-candidate-' + tree + '-'):
                continue
            try:
                return producer(api, repository, artifact, tree)
            except (ValueError, KeyError):
                continue
        if len(rows) < 100:
            break
    return None


def active_preparations(api, repository, tree):
    runs = set()
    # Filter on the server: completed history is unrelated to readiness. Small
    # pages keep verbose GitHub run records within the bounded JSON reader.
    for event in ('pull_request', 'workflow_dispatch'):
        for status in sorted(ACTIVE_RUN_STATUSES):
            for page in range(1, 101):
                rows = api(f'/actions/workflows/macos-app.yaml/runs?per_page=10&page={page}&event={event}&status={status}')['workflow_runs']
                for run in rows:
                    if run.get('status') not in ACTIVE_RUN_STATUSES:
                        continue
                    # Publication uses this same workflow. Only preparation may keep it waiting.
                    if run.get('event') not in ('pull_request', 'workflow_dispatch'):
                        continue
                    if run.get('event') == 'pull_request' and not run.get('head_branch', '').startswith('release-please--branches--'):
                        continue
                    try:
                        preparation_source(api, repository, run, tree)
                        run_id = str(run['id'])
                        if not re.fullmatch(r'[1-9][0-9]*', run_id):
                            continue
                        if run['event'] == 'workflow_dispatch':
                            jobs = api(f'/actions/runs/{run_id}/jobs?per_page=100&filter=latest')['jobs']
                            # A manual publisher has skipped preparation jobs; never wait on itself.
                            if not any(job.get('name', '').endswith(' / portable')
                                       and job.get('conclusion') != 'skipped' for job in jobs):
                                continue
                        runs.add(run_id)
                    except (ValueError, KeyError):
                        continue
                if len(rows) < 10:
                    break
    return runs


def resolve(api, repository, tree):
    selected = find_candidate(api, repository, tree)
    if selected:
        return selected
    runs = active_preparations(api, repository, tree)
    discovered = bool(runs)
    deadline = time.monotonic() + PREPARATION_WAIT
    while runs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Timed out waiting for full release preparation; retry publication after preparation succeeds')
        print(f'Waiting for exact-tree release preparation (runs={",".join(sorted(runs))}); publication has not started', flush=True)
        time.sleep(min(30, remaining))
        pending = set()
        for run_id in sorted(runs):
            # Once bound, poll only that run and its artifacts. Read status first
            # so completion between requests cannot hide the finished archive.
            run = api('/actions/runs/' + run_id)
            if run.get('status') in ACTIVE_RUN_STATUSES:
                pending.add(run_id)
            elif run.get('conclusion') == 'success':
                selected = find_candidate(api, repository, tree, run_id=run_id)
                if selected:
                    return selected
        runs = pending
    # Handle completion during initial discovery; never restart verification.
    selected = None if discovered else find_candidate(api, repository, tree)
    if selected:
        return selected
    raise ValueError('No successful full-verification artifact for this release tree; prepare the release PR or exact tag first')


def receipt(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('Missing bounded release proof')
    return json.loads(path.read_text())


def promote(config, candidate, output, environment, api):
    repository = environment['GITHUB_REPOSITORY']
    source, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    selected = producer(api, repository, api('/actions/artifacts/' + environment['CANDIDATE_ARTIFACT_ID']), tree)
    proof = receipt(candidate / 'candidate.json')
    hashes = files(config, candidate, extras=['candidate.json'])
    expected = dict(schema=1, repository=repository, source=selected['source'], tree=tree,
                    producer_run=selected['producer_run'], policy=policy(), config=config_key(config),
                    verification_key=proof.get('verification_key'), version=version(config, candidate / asset_names(config)[0]), files=hashes)
    if proof != expected or not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', proof['verification_key'] or ''):
        raise ValueError('Candidate proof does not match verified source/policy/bytes')
    if environment['CANDIDATE_TAG'] != config.get('tag_prefix', 'v') + proof['version']:
        raise ValueError('Candidate app version differs from release tag')
    if output.exists() or output.is_symlink():
        raise ValueError('Promotion output must be new')
    output.mkdir(parents=True)
    for name in hashes:
        shutil.copy2(candidate / name, output / name)
    proof.update(target_source=source, artifact_id=selected['artifact_id'])
    (output / 'release-proof.json').write_text(json.dumps(proof))
    names = asset_names(config)
    return dict(archive_sha256=hashes[names[0]], checksum_sha256=hashes[names[1]])


def publication(config, directory, environment, api, *, save=False):
    appcast = environment.get('CANDIDATE_APPCAST', '')
    extras = [appcast] if appcast else []
    if appcast and (appcast != config.get('sparkle', {}).get('appcast') or Path(appcast).name != appcast):
        raise ValueError('Unexpected appcast name')
    path = directory / ('release-proof.json' if save else 'publication.json')
    value = receipt(path)
    hashes = files(config, directory, extras=extras + [path.name])
    hashes.update({name: digest(directory / name) for name in extras})
    source, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    selected = (producer(api, environment['GITHUB_REPOSITORY'], api('/actions/artifacts/' + value['artifact_id']), tree)
                if not save else None)
    if (value.get('target_source') != source or value.get('tree') != tree or value.get('policy') != policy()
            or value.get('repository') != environment['GITHUB_REPOSITORY'] or value.get('config') != config_key(config)
            or value.get('schema') != 1 or not re.fullmatch(SHA, str(value.get('source', '')))
            or not re.fullmatch(r'[1-9][0-9]*', str(value.get('producer_run', '')))
            or not re.fullmatch(r'macos-verified-v1-[a-f0-9]{64}', str(value.get('verification_key', '')))
            or selected is not None and (value.get('source') != selected['source'] or value.get('producer_run') != selected['producer_run'])
            or environment['CANDIDATE_TAG'] != config.get('tag_prefix', 'v') + version(config, directory / asset_names(config)[0])):
        raise ValueError('Publication provenance mismatch')
    if save:
        if value['files'] != {name: hashes[name] for name in asset_names(config)}:
            raise ValueError('Verified archive changed before publication')
        value.update(publication_run=environment['GITHUB_RUN_ID'], tag=environment['CANDIDATE_TAG'], files=hashes)
        (directory / 'publication.json').write_text(json.dumps(value))
        path.unlink()
    else:
        names = asset_names(config)
        expected = {names[0]: environment.get('CANDIDATE_ARCHIVE_SHA256', ''),
                    names[1]: environment.get('CANDIDATE_CHECKSUM_SHA256', '')}
        if (any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in expected.values())
                or any(hashes[name] != value for name, value in expected.items())
                or value.get('publication_run') != environment['GITHUB_RUN_ID']
                or value.get('tag') != environment['CANDIDATE_TAG'] or value['files'] != hashes):
            raise ValueError('Publication bytes/run mismatch')


def main():
    config = json.loads(Path(os.environ['CANDIDATE_CONFIG']).read_text())
    root = Path(os.environ['RUNNER_TEMP'])
    mode = os.environ['CANDIDATE_MODE']
    result = {}
    if mode == 'seal':
        result = seal(config, root / 'macos-release', os.environ,
                      receipt(root / 'macos-verification/validation.json'), json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
    elif mode == 'seal-publication':
        publication(config, root / 'macos-release', os.environ, None, save=True)
    else:
        api = API(os.environ['GITHUB_REPOSITORY'], os.environ['CANDIDATE_TOKEN'])
        if mode == 'resolve':
            result = resolve(api, os.environ['GITHUB_REPOSITORY'], git('rev-parse', 'HEAD^{tree}'))
        elif mode == 'promote':
            result = promote(config, root / 'macos-candidate', root / 'macos-release', os.environ, api)
        elif mode == 'check-publication':
            publication(config, root / 'macos-release', os.environ, api)
        else:
            raise ValueError('Unknown candidate operation')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        for name, value in result.items():
            stream.write(f'{name.replace("_", "-")}={value}\n')


if __name__ == '__main__':
    main()

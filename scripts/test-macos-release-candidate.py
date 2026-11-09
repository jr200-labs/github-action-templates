"""Real Git trees, immutable bytes and adversarial producer metadata."""
import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate', ROOT / 'actions/macos-release-candidate/candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        self.root = Path(temp.name)
        os.chdir(self.root)
        self.command('init', '-q', '-b', 'master')
        Path('source').write_text('tested source')
        self.command('add', 'source')
        self.command('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'test: fixture')
        self.source, self.tree = candidate.git('rev-parse', 'HEAD'), candidate.git('rev-parse', 'HEAD^{tree}')
        self.config = dict(app='dist/Example.app', artifact='example-macos', build=['build'], verify=['verify'])
        self.key = 'macos-verified-v1-' + 'a' * 64
        self.env = dict(GITHUB_REPOSITORY='example/app', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                        GITHUB_EVENT_NAME='pull_request', GITHUB_SHA='b' * 40,
                        CANDIDATE_KEY=self.key, CANDIDATE_FULL='true')
        self.event = dict(pull_request=dict(user=dict(type='Bot'),
                         head=dict(sha=self.source, ref='release-please--branches--master', repo=dict(full_name='example/app'))))
        self.receipt = dict(schema=1, repository='example/app', source=self.source, verification_run='123', key=self.key, execution_seconds=20)
        self.directory = self.root / 'candidate'
        self.directory.mkdir()
        archive = self.directory / 'example-macos.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('Example.app/Contents/Info.plist', plistlib.dumps(dict(CFBundleShortVersionString='1.2.3')))
        (self.directory / 'example-macos.zip.sha256').write_text(candidate.digest(archive) + '  example-macos.zip\n')
        # Generated assets are not tracked source inputs.
        Path('.git/info/exclude').write_text('candidate/\noutput/\n')
        self.name = candidate.seal(self.config, self.directory, self.env, self.receipt, self.event)['name']
        self.artifact = dict(id=12, name=self.name, expired=False,
                             created_at=datetime.now(timezone.utc).isoformat(), workflow_run=dict(id=123))
        self.run = dict(status='completed', conclusion='success', path='.github/workflows/macos-app.yaml',
                        repository=dict(full_name='example/app'), head_repository=dict(full_name='example/app'),
                        head_sha=self.source, head_branch=self.event['pull_request']['head']['ref'], event='pull_request', pull_requests=[],
                        referenced_workflows=[dict(path='jr200-labs/github-action-templates/.github/workflows/build_macos_app.yaml@master', sha='c' * 40)])
        self.publish_env = dict(GITHUB_REPOSITORY='example/app', GITHUB_RUN_ID='456',
                                CANDIDATE_TAG='v1.2.3', CANDIDATE_ARTIFACT_ID='12')
        self.output = self.root / 'output'
        self.calls = []
        self.jobs = [dict(name='app / ' + stage, conclusion='success', run_attempt=1)
                     for stage in ('portable', 'verify', 'package')]
        self.attempt_jobs = copy.deepcopy(self.jobs)

    def command(self, *args):
        subprocess.run(['git', *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def api(self, path):
        self.calls.append(path)
        if path.startswith('/actions/artifacts?'):
            return dict(artifacts=[copy.deepcopy(self.artifact)])
        if path == '/actions/artifacts/12':
            return copy.deepcopy(self.artifact)
        if path == '/actions/runs/123':
            return copy.deepcopy(self.run)
        if path == '/actions/runs/123/jobs?per_page=100&filter=latest':
            return dict(jobs=copy.deepcopy(self.jobs))
        if path == '/actions/runs/123/attempts/1/jobs?per_page=100':
            return dict(jobs=copy.deepcopy(self.attempt_jobs))
        if path == '/git/commits/' + self.source:
            return dict(tree=dict(sha=self.tree))
        if path == '/commits/' + self.source + '/pulls?per_page=100':
            return [copy.deepcopy(self.event['pull_request'])]
        self.fail('Unexpected metadata request: ' + path)

    def promote(self):
        values = candidate.promote(self.config, self.directory, self.output, self.publish_env, self.api)
        self.publish_env.update(CANDIDATE_ARCHIVE_SHA256=values['archive_sha256'], CANDIDATE_CHECKSUM_SHA256=values['checksum_sha256'])

    def test_release_uses_prepared_bytes_across_runs_and_equivalent_merge_commit(self):
        self.command('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'chore: merge release')
        merged = candidate.git('rev-parse', 'HEAD')
        self.assertNotEqual(merged, self.source)
        self.assertEqual(candidate.git('rev-parse', 'HEAD^{tree}'), self.tree)
        selected = candidate.resolve(self.api, 'example/app', self.tree)
        self.assertEqual(selected['producer_run'], '123')
        original = (self.directory / 'example-macos.zip').read_bytes()
        def git(*args):
            self.assertEqual(args[0], 'rev-parse')
            return merged if args[1] == 'HEAD' else self.tree
        with patch.object(candidate, 'git', side_effect=git), \
             patch.object(candidate.subprocess, 'run', side_effect=AssertionError('Build/test/native command during publication')):
            self.promote()
            candidate.publication(self.config, self.output, self.publish_env, None, save=True)
            candidate.publication(self.config, self.output, self.publish_env, self.api)
            before = (self.output / 'publication.json').read_bytes()
            candidate.publication(self.config, self.output, self.publish_env, self.api)
        self.assertEqual((self.output / 'example-macos.zip').read_bytes(), original)
        self.assertEqual((self.output / 'publication.json').read_bytes(), before)
        proof = json.loads(before)
        self.assertEqual(proof['producer_run'], '123')
        self.assertEqual(proof['publication_run'], '456')
        self.assertEqual(proof['target_source'], merged)

    def test_focused_and_ordinary_prs_cannot_certify_a_release(self):
        for env, event, reason in [(dict(self.env, CANDIDATE_FULL='false'), self.event, 'Focused'),
                                   (self.env, dict(pull_request=dict(self.event['pull_request'], user=dict(type='User'))), 'Only full')]:
            with self.subTest(env=env['CANDIDATE_FULL']), self.assertRaisesRegex(ValueError, reason):
                candidate.seal(self.config, self.directory, env, self.receipt, event)

    def test_invalid_producers_and_other_source_trees_are_rejected(self):
        changes = [dict(conclusion='failure'), dict(status='in_progress'), dict(event='push'),
                   dict(head_repository=dict(full_name='other/fork')), dict(repository=dict(full_name='other/app')),
                   dict(path='.github/workflows/other.yaml'), dict(referenced_workflows=[])]
        original = self.run
        for change in changes:
            with self.subTest(change=change):
                self.run = dict(original, **change)
                with self.assertRaises(ValueError):
                    candidate.producer(self.api, 'example/app', self.artifact, self.tree)
        self.run = original
        with self.assertRaises(ValueError):
            candidate.producer(self.api, 'example/app', self.artifact, 'd' * 40)

    def test_expired_missing_and_untrusted_release_pr_artifacts_fail_closed(self):
        self.artifact['expired'] = True
        with self.assertRaisesRegex(ValueError, 'No successful'):
            candidate.resolve(self.api, 'example/app', self.tree)
        self.artifact['expired'] = False
        self.artifact['created_at'] = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        with self.assertRaises(ValueError):
            candidate.producer(self.api, 'example/app', self.artifact, self.tree)
        self.artifact['created_at'] = datetime.now(timezone.utc).isoformat()
        self.event['pull_request']['head']['repo']['full_name'] = 'other/fork'
        with self.assertRaises(ValueError):
            candidate.producer(self.api, 'example/app', self.artifact, self.tree)

    def test_archive_version_source_policy_and_file_tampering_are_rejected(self):
        proof_path = self.directory / 'candidate.json'
        original = json.loads(proof_path.read_text())
        for field, value in [('tree', 'd' * 40), ('source', 'd' * 40), ('policy', '0' * 64), ('config', '0' * 64), ('version', '9.9.9')]:
            proof_path.write_text(json.dumps(dict(original, **{field: value})))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.promote()
        proof_path.write_text(json.dumps(original))
        self.publish_env['CANDIDATE_TAG'] = 'v9.9.9'
        with self.assertRaisesRegex(ValueError, 'version'):
            self.promote()
        self.publish_env['CANDIDATE_TAG'] = 'v1.2.3'
        (self.directory / 'example-macos.zip.sha256').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.promote()

    def test_publisher_cannot_replace_bytes_even_with_a_rewritten_manifest(self):
        self.promote()
        candidate.publication(self.config, self.output, self.publish_env, None, save=True)
        archive = self.output / 'example-macos.zip'
        with zipfile.ZipFile(archive, 'a') as output:
            output.writestr('Example.app/changed', b'altered')
        (self.output / 'example-macos.zip.sha256').write_text(candidate.digest(archive) + '  example-macos.zip\n')
        proof = json.loads((self.output / 'publication.json').read_text())
        proof['files'] = {name: candidate.digest(self.output / name) for name in candidate.asset_names(self.config)}
        (self.output / 'publication.json').write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, 'bytes/run'):
            candidate.publication(self.config, self.output, self.publish_env, self.api)

    def test_api_permission_errors_do_not_trigger_reverification(self):
        with self.assertRaisesRegex(RuntimeError, '403'):
            candidate.resolve(lambda path: (_ for _ in ()).throw(RuntimeError('403')), 'example/app', self.tree)

    def test_publication_or_focused_workflow_success_is_not_full_verification_proof(self):
        self.run.update(event='workflow_dispatch', pull_requests=[])
        self.jobs = [dict(name='release / publish', conclusion='success', run_attempt=1)]
        with self.assertRaisesRegex(ValueError, 'preparation jobs'):
            candidate.producer(self.api, 'example/app', self.artifact, self.tree)
        self.jobs = [dict(name='app / ' + stage, conclusion='skipped' if stage == 'verify' else 'success', run_attempt=1)
                     for stage in ('portable', 'verify', 'package')]
        with self.assertRaisesRegex(ValueError, 'preparation jobs'):
            candidate.producer(self.api, 'example/app', self.artifact, self.tree)

    def test_failed_packaging_attempt_is_not_accepted(self):
        self.attempt_jobs[-1]['conclusion'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'packaging attempt'):
            candidate.producer(self.api, 'example/app', self.artifact, self.tree)

    def test_metadata_client_is_read_only_and_repository_scoped(self):
        api = candidate.API('example/app', 'token-' + 'x' * 30)
        with patch.object(candidate, 'urlopen', side_effect=AssertionError('outside-scope request')):
            for path in ['/repos/other/app/actions/artifacts', '/releases/1', '/actions/runs/1/cancel', '/git/commits/not-a-sha']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    api(path)

    def test_metadata_client_uses_fixed_origin_get_and_bounded_json(self):
        api = candidate.API('example/app', 'token-' + 'x' * 30)
        with patch.object(candidate, 'urlopen') as opened:
            opened.return_value.__enter__.return_value.read.return_value = b'{"jobs": []}'
            self.assertEqual(api('/actions/runs/123/jobs?per_page=100&filter=latest'), dict(jobs=[]))
            request = opened.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://api.github.com/repos/example/app/actions/runs/123/jobs?per_page=100&filter=latest')
            opened.return_value.__enter__.return_value.read.assert_called_once_with(1_048_577)


class WorkflowTest(unittest.TestCase):
    def workflow(self, name):
        return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows' / name)], text=True))

    def test_publication_has_no_build_verification_or_simulator_path(self):
        build = self.workflow('build_macos_app.yaml')
        publish = self.workflow('publish_macos_app.yaml')
        self.assertEqual(build['jobs']['release']['if'], 'inputs.publish-release')
        for name in ('portable', 'verify', 'package'):
            self.assertEqual(build['jobs'][name]['if'], '${{ !inputs.publish-release }}')
        text = json.dumps(publish)
        for forbidden in ('macos-verify@', 'macos-package@', 'stage-identity', 'xcodebuild', 'simctl', 'native_work', 'build-macos-app', 'build_macos_app.yaml'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(publish['jobs']), {'resolve', 'sign', 'publish'})
        self.assertEqual(publish['jobs']['sign']['permissions'], {'contents': 'read'})
        self.assertNotIn('github.token', json.dumps(publish['jobs']['sign']))

    def test_release_pr_uses_head_tree_and_full_preparation_without_signing_secrets(self):
        build = self.workflow('build_macos_app.yaml')
        for name in ('portable', 'verify', 'package'):
            checkout = build['jobs'][name]['steps'][0]
            self.assertIn('github.event.pull_request.head.sha', checkout['with']['ref'])
        for name in ('verify', 'package'):
            payload = json.dumps(build['jobs'][name])
            self.assertIn('MACOS_PACKAGE_RELEASE_ASSETS', payload)
            self.assertIn('release-please--branches--', payload)
            self.assertNotIn('SPARKLE_EDDSA_PRIVATE_KEY', payload)
        steps = build['jobs']['package']['steps']
        names = [step.get('name') for step in steps]
        self.assertLess(names.index('Build package'), names.index('Seal fully verified release candidate'))
        uploaded = next(step for step in steps if step.get('name') == 'Retain fully verified release candidate')
        self.assertEqual(uploaded['with']['retention-days'], 5)
        self.assertIs(uploaded['with']['overwrite'], False)
        self.assertNotIn('actions/cache', json.dumps(build))

    def test_publication_retries_use_successful_job_outputs_and_resolver_digests(self):
        publish = self.workflow('publish_macos_app.yaml')
        job = publish['jobs']['publish']
        downloaded = next(step for step in job['steps'] if step.get('name') == 'Download signed publication bundle')
        self.assertEqual(downloaded['with']['name'], '${{ needs.sign.outputs.bundle-name }}')
        checked = next(step for step in job['steps'] if step.get('name') == 'Check publication provenance and bytes')
        self.assertEqual(checked['with']['archive-sha256'], '${{ needs.resolve.outputs.archive-sha256 }}')
        self.assertEqual(checked['with']['checksum-sha256'], '${{ needs.resolve.outputs.checksum-sha256 }}')
        self.assertIn('github.run_attempt', publish['jobs']['resolve']['outputs']['bundle-name'])
        self.assertIn('github.run_attempt', publish['jobs']['sign']['outputs']['bundle-name'])


if __name__ == '__main__':
    unittest.main()

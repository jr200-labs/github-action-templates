"""Execute the publication workflow's source-capture step against real Git."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

class BuildProvenance(unittest.TestCase):
    def test_source_is_checked_out_tree_not_trigger_commit(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/build_docker_image_multiplatform.yaml').read_text())
        steps = workflow['jobs']['publish']['steps']
        step = next(s for s in steps if s.get('id') == 'source-provenance')
        for build in (s for s in steps if s.get('uses', '').startswith('docker/build-push-action@')):
            for name, value in [('SOURCE_REVISION', 'revision'), ('SOURCE_DIRTY', 'dirty')]:
                self.assertIn(name + '=${{ steps.source-provenance.outputs.' + value + ' }}', build['with']['build-args'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            (root / 'source').write_text('initial')
            git('add', 'source')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'initial')
            revision = git('rev-parse', 'HEAD')
            for dirty in (False, True):
                if dirty:
                    (root / 'source').write_text('modified')
                output = Path(directory) / 'output'
                output.write_text('')
                subprocess.run(['bash', '-c', step['run']], cwd=root, check=True,
                               env=dict(os.environ, GITHUB_OUTPUT=str(output), GITHUB_SHA='unrelated-trigger'))
                self.assertEqual(dict(line.split('=', 1) for line in output.read_text().splitlines()),
                                 {'revision': revision, 'dirty': str(dirty).lower()})

if __name__ == '__main__':
    unittest.main()

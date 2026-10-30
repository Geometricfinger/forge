"""Public identities must not exempt adjacent private data or longer addresses."""
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import prepublish_check as check


class PrepublishCheckTests(unittest.TestCase):
    def setUp(self):
        self.public = 'maintainer' + '@' + 'mail.dev'
        self.other = 'unapproved' + '@' + 'mail.dev'
        self.patterns = {name: re.compile(rx) for name, rx in check.BUILTIN.items()}
        self.allow = [
            {'path': path, 'contains': self.public}
            for path in ('README.md', 'git:commit-metadata')
        ]

    def scan(self, text, path='README.md', allow=None):
        findings = []
        check.scan_text(path, text, self.patterns,
                        self.allow if allow is None else allow, findings, path)
        return [finding['rule'] for finding in findings]

    def test_exact_public_identity_can_appear_more_than_once(self):
        for path in ('README.md', 'git:commit-metadata'):
            with self.subTest(path=path):
                self.assertEqual(self.scan(self.public + '\0' + self.public, path), [])

    def test_unapproved_email_still_fails_beside_public_identity(self):
        for path in ('README.md', 'git:commit-metadata'):
            for separator in (' ', '\0', '\n'):
                with self.subTest(path=path, separator=separator):
                    self.assertEqual(self.scan(self.public + separator + self.other, path), ['email'])

    def test_longer_email_is_not_the_approved_identity(self):
        for path in ('README.md', 'git:commit-metadata'):
            for address in ('extra.' + self.public, self.public + '.com'):
                with self.subTest(path=path, address=address):
                    self.assertEqual(self.scan(address, path), ['email'])

    def test_allowlist_is_scoped_to_exact_path(self):
        self.assertEqual(self.scan(self.public, 'notes.md'), ['email'])
        self.assertEqual(self.scan(self.public, 'docs/README.md'), ['email'])

    def test_empty_literal_exempts_nothing(self):
        self.assertEqual(self.scan(self.other, allow=[{'path': 'README.md', 'contains': ''}]), ['email'])

    def test_secret_beside_or_containing_public_identity_still_fails(self):
        for value in ('synthetic-test-value', self.public):
            for path in ('README.md', 'git:commit-metadata'):
                with self.subTest(value=value, path=path):
                    text = self.public + ' api_' + 'key=' + repr(value)
                    self.assertEqual(self.scan(text, path), ['assigned_secret'])

    def test_private_denylist_term_beside_public_identity_still_fails(self):
        self.patterns['denylist_line_1'] = re.compile('restricted-fixture', re.I)
        for path in ('README.md', 'git:commit-metadata'):
            with self.subTest(path=path):
                self.assertEqual(self.scan(self.public + ' restricted-fixture', path), ['denylist_line_1'])

    def test_contextual_template_exception_only_covers_its_own_match(self):
        host = 'drive.' + 'google.com/'
        template = 'https://' + host + 'file/d/<file_id>/view'
        allow = [{'path': 'README.md', 'contains': template}]
        self.assertEqual(self.scan(template, allow=allow), [])
        self.assertEqual(self.scan(template + ' https://' + host + 'private', allow=allow), ['drive_link'])

    def history_report(self, message, historical_email=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            (root / 'tools/prepublish_allowlist.json').write_text(json.dumps({'entries': self.allow}))
            readme = root / 'README.md'
            readme.write_text(self.public + (' ' + historical_email if historical_email else ''))

            def git(*args):
                subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test Maintainer',
                                '-c', 'user.email=' + self.public, *args],
                               check=True, capture_output=True)

            git('init', '--quiet')
            git('add', '.')
            git('commit', '--quiet', '-m', message)
            if historical_email:
                readme.write_text(self.public)
                git('add', 'README.md')
                git('commit', '--quiet', '-m', 'Remove historical fixture')
            output = io.StringIO()
            with patch.object(check, 'ROOT', root), patch('sys.argv', ['prepublish_check', '--history', '--denylist', '']), contextlib.redirect_stdout(output):
                code = check.main()
            return code, json.loads(output.getvalue())

    def test_history_accepts_intentionally_public_author_and_committer(self):
        code, report = self.history_report('Public identity fixture')
        self.assertEqual(code, 0)
        self.assertEqual(report['findings'], 0)
        self.assertEqual(report['history'], 'scanned 2 blobs')

    def test_history_still_scans_commit_messages_and_removed_private_blobs(self):
        code, report = self.history_report(self.public + ' ' + self.other, self.other)
        self.assertEqual(code, 1)
        self.assertEqual(report['findings'], 2)
        self.assertTrue(any(row['where'] == 'git:commit-metadata' for row in report['details']))
        self.assertTrue(any(row['where'].startswith('git:README.md@') for row in report['details']))
        self.assertEqual({row['rule'] for row in report['details']}, {'email'})


if __name__ == '__main__':
    unittest.main()

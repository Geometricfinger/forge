"""Publication checks reject disclosure without echoing the rejected material."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import prepublish_check as check


class PublicationPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.value = 'gh' + 'p_' + 'Z' * 40
        self.email = 'former-contact' + '@' + 'mail.dev'
        self.public = 'test-maintainer' + '@' + 'mail.dev'
        self.patterns = {name: re.compile(rx) for name, rx in check.BUILTIN.items()}
        self.allow = [{'path': path, 'contains': self.public,
                       'reason': 'Synthetic public test identity.'}
                      for path in ('README.md', 'git:commit-metadata')]
        self.historical = [
            {'path': path, 'match_sha256': hashlib.sha256(self.email.encode()).hexdigest(),
             'rule': 'email', 'history_only': True, 'reason': 'Synthetic existing history identity.'}
            for path in ('README.md', 'git:commit-metadata')
        ]

    def scan(self, text, path='notes.json', allow=None, history=False):
        findings = []
        check.scan_text(path, text, self.patterns, self.allow if allow is None else allow,
                        findings, path, history=history)
        return findings

    def run_main(self, root, *, history=False, denylist=''):
        output = io.StringIO(); errors = io.StringIO()
        args = ['prepublish_check', '--denylist', str(denylist)]
        if history:
            args.append('--history')
        with patch.object(check, 'ROOT', root), patch('sys.argv', args), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = check.main()
        return code, json.loads(output.getvalue()), output.getvalue() + errors.getvalue()

    def policy(self, root, entries=None, **extra):
        (root / 'tools').mkdir(exist_ok=True)
        data = {'entries': self.allow if entries is None else entries, **extra}
        (root / check.ALLOWLIST_PATH).write_text(json.dumps(data))

    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test Maintainer',
                               '-c', 'user.email=' + self.public, *args],
                              capture_output=True, check=True)

    def commit(self, root, message):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '--quiet', '-m', message)

    def test_json_secret_keys_and_session_fields_are_checked(self):
        for key in ('api_key', 'access_token', 'refresh_token', 'client_secret',
                    'session_token', 'session_secret', 'token', 'password'):
            with self.subTest(key=key):
                result = self.scan(json.dumps({key: 'q' * 32}))
                self.assertIn('assigned_secret', {row['rule'] for row in result})

    def test_quoted_authorization_and_cookie_headers_are_checked(self):
        for text in (json.dumps({'Authorization': 'Bearer ' + 'q' * 32}),
                     json.dumps({'Authorization': 'Basic ' + 'q' * 32}),
                     'Cookie: session=' + 'q' * 32,
                     'Set-Cookie: session=' + 'q' * 32):
            with self.subTest(header=text.split(':', 1)[0]):
                self.assertTrue(self.scan(text))

    def test_credential_query_parameters_are_checked(self):
        for key in ('token', 'access_token', 'api_key', 'sig', 'X-Amz-Signature'):
            self.assertIn('credential_url_parameter', {
                row['rule'] for row in self.scan('https://example.org/fixture?' + key + '=' + 'q' * 24)})

    def test_utf16_and_utf32_credentials_are_checked(self):
        text = json.dumps({'access_token': 'q' * 32})
        for encoding in ('utf-16', 'utf-32', 'utf-16le', 'utf-16be', 'utf-32le', 'utf-32be'):
            with self.subTest(encoding=encoding):
                result = []
                check.scan_bytes('encoded.txt', text.encode(encoding), self.patterns, [], result)
                self.assertIn('assigned_secret', {row['rule'] for row in result})

    def test_scanner_source_is_not_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            (root / 'tools/prepublish_check.py').write_text(json.dumps({'access_token': self.value}))
            code, report, output = self.run_main(root)
            self.assertEqual(code, 1)
            self.assertTrue(any(row['where'] == 'tools/prepublish_check.py' for row in report['details']))
            self.assertNotIn(self.value, output)

    def test_allowlist_note_is_not_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root, note=self.value)
            code, report, output = self.run_main(root)
            self.assertEqual(code, 1)
            self.assertTrue(any(row['where'] == check.ALLOWLIST_PATH for row in report['details']))
            self.assertNotIn(self.value, output)

    def test_only_exact_approved_contains_value_is_exempt(self):
        policy = {'entries': self.allow, 'note': self.value}
        rows = self.scan(json.dumps(policy), check.ALLOWLIST_PATH)
        self.assertEqual({row['rule'] for row in rows}, {'github_token'})
        for key in ('note', 'reason'):
            value = {'entries': [dict(self.allow[0], reason=self.value)]} if key == 'reason' else policy
            self.assertTrue(self.scan(json.dumps(value), check.ALLOWLIST_PATH))

    def test_historical_identity_cannot_exempt_current_source_or_artifacts(self):
        for path in ('README.md', 'git:commit-metadata'):
            self.assertEqual([row['rule'] for row in self.scan(self.email, path, self.historical)], ['email'])
            self.assertEqual(self.scan(self.email, path, self.historical, history=True), [])
        self.assertTrue(self.scan(self.email, 'other.md', self.historical, history=True))

    def test_historical_identity_cannot_exempt_changed_or_adjacent_content(self):
        for value in ('extra.' + self.email, self.email + '.com',
                      self.email + ' ' + self.public):
            self.assertTrue(self.scan(value, 'README.md', self.historical, history=True))
        result = self.scan(json.dumps({'password': self.email}), 'README.md', self.historical, history=True)
        self.assertEqual({row['rule'] for row in result}, {'assigned_secret'})

    def test_legacy_allowlist_identity_is_allowed_only_in_history_value_span(self):
        old = {'entries': [{'path': 'README.md', 'contains': self.email, 'reason': 'Old contact.'}]}
        text = json.dumps(old)
        self.assertTrue(self.scan(text, check.ALLOWLIST_PATH, self.historical))
        self.assertEqual(self.scan(text, check.ALLOWLIST_PATH, self.historical, history=True), [])
        old['note'] = self.email
        self.assertTrue(self.scan(json.dumps(old), check.ALLOWLIST_PATH, self.historical, history=True))

    def test_history_selector_requires_exact_valid_schema(self):
        invalid = [
            dict(self.historical[0], history_only=False),
            dict(self.historical[0], rule='github_token'),
            dict(self.historical[0], match_sha256='not-a-digest'),
            dict(self.allow[0], extra='unreviewed'),
            dict(self.allow[0], contains=''),
        ]
        for entry in invalid:
            with self.subTest(fields=sorted(entry)):
                with self.assertRaises(ValueError):
                    check._policy_entries(json.dumps({'entries': [entry]}))

    def test_scanner_rejects_secret_in_removed_scanner_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            path = root / 'tools/prepublish_check.py'
            path.write_text(json.dumps({'access_token': self.value}))
            self.git(root, 'init', '--quiet'); self.commit(root, 'Initial synthetic fixture')
            path.write_text('# Current harmless fixture\n'); self.commit(root, 'Remove synthetic fixture')
            code, report, output = self.run_main(root, history=True)
            self.assertEqual(code, 1)
            self.assertTrue(any(row['where'].startswith('git:tools/prepublish_check.py@')
                                for row in report['details']))
            self.assertNotIn(self.value, output)

    def test_history_checks_every_path_of_a_reused_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            (root / 'README.md').write_text(self.public)
            (root / 'private-notes.md').write_text(self.public)
            self.git(root, 'init', '--quiet'); self.commit(root, 'Two paths share a fixture blob')
            (root / 'private-notes.md').unlink(); self.commit(root, 'Remove unapproved path')
            code, report, output = self.run_main(root, history=True)
            self.assertEqual(code, 1)
            self.assertTrue(any(row['where'].startswith('git:private-notes.md@')
                                for row in report['details']))
            self.assertNotIn(self.public, output)

    def test_secret_filename_and_matched_values_are_fully_redacted(self):
        findings = []
        label = 'notes/' + self.value + '.json'
        check.scan_name(label, self.patterns, findings)
        check.scan_text(label, self.value, self.patterns, [], findings)
        encoded = json.dumps(findings)
        self.assertTrue(findings)
        self.assertNotIn(self.value, encoded)
        self.assertNotIn(self.value[:8], encoded)
        self.assertTrue(all('[redacted]' in row['where'] for row in findings))

    def test_sensitive_config_filename_is_rejected_even_when_empty(self):
        findings = []
        check.scan_name('config/.env', self.patterns, findings)
        self.assertEqual([row['rule'] for row in findings], ['sensitive_file_name'])

    def test_private_denylist_regex_errors_do_not_echo_term_or_path(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory); root = outer / 'repo'; root.mkdir(); self.policy(root)
            private = outer / (self.value + '.txt')
            private.write_text(self.value + '[')
            code, report, output = self.run_main(root, denylist=private)
            self.assertEqual(code, 2)
            self.assertEqual(report['reason'], 'INVALID_OR_UNREADABLE_POLICY')
            self.assertNotIn(self.value, output)
            self.assertNotIn(str(private), output)

    def test_malformed_policy_does_not_echo_content_or_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            (root / check.ALLOWLIST_PATH).write_text('{"entries": [' + self.value)
            code, report, output = self.run_main(root)
            self.assertEqual(code, 2)
            self.assertEqual(report['reason'], 'INVALID_OR_UNREADABLE_POLICY')
            self.assertNotIn(self.value, output)
            self.assertNotIn('Traceback', output)

    def test_duplicate_policy_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            (root / check.ALLOWLIST_PATH).write_text('{"entries": [], "entries": []}')
            self.assertEqual(self.run_main(root)[0], 2)

    def test_unreadable_file_does_not_echo_private_error_or_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.policy(root)
            (root / 'unreadable.txt').write_text('synthetic input')
            with patch.object(Path, 'read_bytes', side_effect=OSError(self.value)):
                code, report, output = self.run_main(root)
            self.assertEqual(code, 2)
            self.assertEqual(report['reason'], 'INVALID_OR_UNREADABLE_SCAN_INPUT')
            self.assertNotIn(self.value, output)
            self.assertNotIn(str(root), output)

    def test_symlink_is_rejected_without_reading_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory); root = outer / 'repo'; root.mkdir(); self.policy(root)
            target = outer / 'private-data.txt'; target.write_text(self.value)
            link = root / 'linked.txt'; link.symlink_to(target)
            original = Path.read_bytes
            def read_bytes(path):
                if path == link:
                    raise AssertionError('Must not read symlink')
                return original(path)
            with patch.object(Path, 'read_bytes', read_bytes):
                code, report, output = self.run_main(root)
            self.assertEqual(code, 1)
            self.assertEqual([row['rule'] for row in report['details']], ['symlink_entry'])
            self.assertNotIn(self.value, output)


if __name__ == '__main__':
    unittest.main()

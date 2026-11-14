"""Publication refuses unsafe evidence before it creates an upload directory."""
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import prepare_public_evidence as gate


COMMIT = '1' * 40


def digest(data):
    return hashlib.sha256(data).hexdigest()


def packet(files=None, *, passed=True):
    result = dict(files or {})
    report = {'status': 'PASSED_FOR_LOCAL_DECLARED_SCOPE' if passed else 'FAILED'}
    result['qualification/verification.json'] = json.dumps(report).encode()
    provenance = {'commit_sha': COMMIT, 'job_status': 'success' if passed else 'failure',
                  'verification_status': report['status'],
                  'verification_sha256': digest(result['qualification/verification.json']),
                  'files': {name: digest(data) for name, data in result.items()}}
    result['provenance.json'] = json.dumps(provenance, sort_keys=True).encode()
    result['SHA256SUMS.txt'] = ''.join(digest(data) + '  ./' + name + '\n'
                                     for name, data in sorted(result.items())).encode()
    return result


def zipped(files, *, comment=b''):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        archive.comment = comment
    return data.getvalue()


class PublicEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / 'evidence'; self.source.mkdir()
        self.out = self.base / 'publish'
        self.token = 'gh' + 'p_' + 'A' * 40

    def write(self, files):
        for name, body in files.items():
            p = self.source / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)

    def prepare(self, files=None, **kwargs):
        self.write(packet(files, passed=kwargs.pop('passed', True)))
        return gate.prepare(self.source, self.out, COMMIT, **kwargs)

    def blocked(self, files, **kwargs):
        result = self.prepare(files, **kwargs)
        self.assertEqual(result['status'], 'PUBLICATION_BLOCKED')
        self.assertGreater(result['findings'], 0)
        self.assertFalse(self.out.exists())
        self.assertNotIn(self.token, json.dumps(result))
        return result

    def test_safe_evidence_is_staged_with_exact_bytes(self):
        result = self.prepare({'result.log': b'14 checks passed\n', '.scan.lock': b''})
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')
        self.assertEqual(gate.collect(self.source), gate.collect(self.out))
        self.assertTrue(result['exact_bytes_preserved'])
        self.assertFalse(result['secret_free_guarantee'])

    def test_intact_failed_qualification_remains_failed_evidence(self):
        result = self.prepare({'result.log': b'An expected fixture failed\n'}, passed=False)
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')
        self.assertEqual(result['qualification_status'], 'FAILED')
        self.assertFalse(result['reported_qualification_passed'])

    def test_only_original_excluded_home_is_omitted(self):
        private = self.source / 'qualification/home/private.txt'
        private.parent.mkdir(parents=True); private.write_text(self.token)
        result = self.prepare({'qualification/home-other/result.txt': b'public fixture'})
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')
        self.assertFalse((self.out / 'qualification/home').exists())
        self.assertTrue((self.out / 'qualification/home-other/result.txt').is_file())

    def test_ordinary_text_credentials_block(self):
        self.blocked({'output.log': self.token.encode()})

    def test_unsupported_binary_content_cannot_be_silently_skipped(self):
        self.write(packet({'output.bin': b'\0' + self.token.encode() + b'\xff'}))
        with self.assertRaisesRegex(gate.PublicationError, 'UNSUPPORTED_BINARY_CONTENT'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_quoted_json_secret_assignment_blocks(self):
        self.blocked({'result.json': json.dumps({'api_' + 'key': 'fixture-value-only-9472'}).encode()})

    def test_escaped_json_token_is_decoded_before_checking(self):
        escaped = ''.join('\\u%04x' % ord(c) for c in self.token)
        self.blocked({'result.json': ('{"value":"' + escaped + '"}').encode()})

    def test_escaped_json_field_name_preserves_credential_context(self):
        key = ''.join('\\u%04x' % ord(c) for c in 'api_' + 'key')
        self.blocked({'result.json': ('{"' + key + '":"fixture-value-only-9472"}').encode()})

    def test_notebook_and_har_json_strings_are_decoded(self):
        escaped = ''.join('\\u%04x' % ord(c) for c in self.token)
        files = {name: ('{"value":"' + escaped + '"}').encode()
                 for name in ('notebook.ipynb', 'recording.har')}
        self.blocked(files)

    def test_terminal_cast_json_lines_are_decoded(self):
        escaped = ''.join('\\u%04x' % ord(c) for c in self.token)
        self.blocked({'recording.cast': ('{"version":2}\n[0.1,"o","' + escaped + '"]\n').encode()})

    def test_utf16_text_is_checked(self):
        self.blocked({'result.txt': self.token.encode('utf-16')})

    def test_hidden_credential_file_names_block_without_echoing_them(self):
        result = self.blocked({'.env.local': b'harmless fixture'})
        self.assertIn('credential_file_name', result['rules'])
        self.assertNotIn('.env.local', json.dumps(result))

    def test_secret_in_file_name_is_not_exposed_by_diagnostics(self):
        name = self.token + '.txt'
        result = self.blocked({name: b'harmless fixture'})
        self.assertNotIn(name, json.dumps(result))

    def test_gzip_contents_are_checked(self):
        self.blocked({'result.log.gz': gzip.compress(self.token.encode())})

    def test_gzip_original_filename_is_checked(self):
        data = io.BytesIO()
        with gzip.GzipFile(fileobj=data, filename=self.token, mode='wb') as stream:
            stream.write(b'harmless fixture')
        self.blocked({'result.gz': data.getvalue()})

    def test_concatenated_gzip_members_do_not_hide_split_token(self):
        self.blocked({'result.gz': gzip.compress(self.token[:4].encode()) +
                      gzip.compress(self.token[4:].encode())})

    def test_nested_zip_gzip_contents_are_checked(self):
        self.blocked({'outer.zip': zipped({'inner.zip': zipped({'value.gz': gzip.compress(self.token.encode())})})})

    def test_zip_comments_are_checked(self):
        self.blocked({'result.zip': zipped({'ok.txt': b'harmless'}, comment=self.token.encode())})

    def test_sqlite_overflow_value_is_reassembled_and_checked(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('PRAGMA page_size=512')
        connection.execute('CREATE TABLE observations(value TEXT)')
        connection.execute('INSERT INTO observations VALUES (?)', ('x' * 700 + ' ' + self.token + ' ' + 'y' * 900,))
        data = connection.serialize(); connection.close()
        result = self.blocked({'results.sqlite': data})
        self.assertEqual(result['container_counts']['sqlite_databases'], 1)
        self.assertGreaterEqual(result['container_counts']['sqlite_rows'], 1)

    def test_sqlite_views_and_virtual_generated_columns_are_not_evaluated(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE observations(value TEXT, unused AS (json_extract(value, \'$.x\')) VIRTUAL)')
        connection.execute('CREATE VIEW untrusted AS SELECT load_extension(\'does-not-exist\')')
        connection.execute('INSERT INTO observations(value) VALUES (?)', ('{"x":7}',))
        data = connection.serialize(); connection.close()
        result = self.prepare({'results.sqlite': data})
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')

    def test_sqlite_column_name_preserves_credential_context(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE observations("api_' + 'key" TEXT)')
        connection.execute('INSERT INTO observations VALUES (?)', ('fixture-value-only-9472',))
        data = connection.serialize(); connection.close()
        self.blocked({'results.sqlite': data})

    def test_sqlite_key_value_row_preserves_credential_context(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE settings(key TEXT, value TEXT)')
        connection.execute('INSERT INTO settings VALUES (?,?)', ('api_' + 'key', 'fixture-value-only-9472'))
        data = connection.serialize(); connection.close()
        self.blocked({'results.sqlite': data})

    def test_historical_identity_exceptions_never_apply_to_artifacts(self):
        allow = [{'path': 'hound/tests/test_hound.py', 'contains': 'fixture',
                  'history_only': True, 'rule': 'email', 'match_sha256': '0' * 64}]
        with patch.object(gate.privacy, 'load_allowlist', return_value=allow):
            self.assertEqual(gate.source_exceptions(gate.ROOT), {})

    def test_only_exact_runner_home_with_opt_in_is_allowed(self):
        self.blocked({'result.log': ('/home/' + 'runner/work/forge').encode()})
        files = packet({'result.log': ('/home/' + 'runner/work/forge').encode()})
        report = gate.scan_files(files, ci_runner_paths=True)
        self.assertEqual(report['findings'], 0)
        report = gate.scan_files(packet({'result.log': ('/home/' + 'runner.private/work').encode()}),
                                 ci_runner_paths=True)
        self.assertGreater(report['findings'], 0)

    def test_exact_public_source_fixture_can_be_copied(self):
        relative = 'hound/tests/test_hound.py'
        body = (gate.ROOT / relative).read_bytes()
        result = self.prepare({'runtime/' + relative: body})
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')
        self.assertGreater(result['container_counts']['source_fixture_matches'], 0)

    def test_changed_source_copy_has_no_fixture_exception(self):
        relative = 'hound/tests/test_hound.py'
        self.blocked({'runtime/' + relative: (gate.ROOT / relative).read_bytes() + b'\n# changed\n'})

    def test_source_exception_requires_original_path(self):
        self.blocked({'wrong.py': (gate.ROOT / 'hound/tests/test_hound.py').read_bytes()})

    def test_private_denylist_matches_are_redacted(self):
        term = 'private-fixture-organization'
        deny = self.base / 'private-denylist.txt'; deny.write_text(term)
        result = self.blocked({term + '.txt': term.encode()}, denylist=deny)
        self.assertNotIn(term, json.dumps(result))

    def test_invalid_manifest_membership_blocks_before_output(self):
        self.write(packet({'known.txt': b'fixture'}))
        (self.source / 'unlisted.txt').write_text('extra')
        with self.assertRaisesRegex(gate.PublicationError, 'FILE_SET_MISMATCH'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_changed_bound_bytes_block_before_output(self):
        self.write(packet({'known.txt': b'fixture'}))
        (self.source / 'known.txt').write_text('changed')
        with self.assertRaisesRegex(gate.PublicationError, 'FILE_HASH_MISMATCH'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_wrong_commit_is_rejected(self):
        self.write(packet())
        with self.assertRaisesRegex(gate.PublicationError, 'COMMIT_MISMATCH'):
            gate.prepare(self.source, self.out, '2' * 40)
        self.assertFalse(self.out.exists())

    def test_untrusted_qualification_status_is_not_returned(self):
        status = 'private-status-text-only'
        files = packet()
        report = json.dumps({'status': status}).encode()
        files['qualification/verification.json'] = report
        provenance = json.loads(files['provenance.json'])
        provenance['verification_status'] = status
        provenance['verification_sha256'] = digest(report)
        provenance['files']['qualification/verification.json'] = digest(report)
        files['provenance.json'] = json.dumps(provenance).encode()
        files['SHA256SUMS.txt'] = ''.join(digest(data) + '  ./' + name + '\n'
            for name, data in sorted(files.items()) if name != 'SHA256SUMS.txt').encode()
        result = gate.verify_files(files, COMMIT)
        self.assertEqual(result['qualification_status'], 'OTHER')
        self.assertNotIn(status, json.dumps(result))

    def test_output_overlap_and_existing_output_are_rejected(self):
        self.write(packet())
        with self.assertRaisesRegex(gate.PublicationError, 'SEPARATE_DIRECTORIES_REQUIRED'):
            gate.prepare(self.source, self.source / 'publish', COMMIT)
        self.out.mkdir()
        with self.assertRaisesRegex(gate.PublicationError, 'NEW_OUTPUT_REQUIRED'):
            gate.prepare(self.source, self.out, COMMIT)

    def test_symlinked_input_member_is_rejected(self):
        self.write(packet())
        (self.source / 'link').symlink_to(self.base / 'private')
        with self.assertRaisesRegex(gate.PublicationError, 'FILE_TYPE_OR_SIZE'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_nested_archive_symlink_is_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            info = zipfile.ZipInfo('link')
            info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'target')
        self.write(packet({'bad.zip': data.getvalue()}))
        with self.assertRaisesRegex(gate.PublicationError, 'UNSAFE_ZIP_MEMBER'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_malformed_compressed_data_cannot_be_silently_skipped(self):
        self.write(packet({'broken.gz': b'not a gzip file'}))
        with self.assertRaisesRegex(gate.PublicationError, 'UNREADABLE_GZIP'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_decoded_size_limit_blocks_before_output(self):
        self.write(packet({'large.gz': gzip.compress(b'x' * 300_000)}))
        with patch.object(gate, 'MAX_FILE_BYTES', 200_000):
            with self.assertRaisesRegex(gate.PublicationError, 'GZIP_LIMIT'):
                gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_nested_depth_limit_blocks_before_output(self):
        value = b'fixture'
        for _ in range(gate.MAX_DEPTH + 1):
            value = gzip.compress(value)
        self.write(packet({'nested.gz': value}))
        with self.assertRaisesRegex(gate.PublicationError, 'NESTING_LIMIT'):
            gate.prepare(self.source, self.out, COMMIT)
        self.assertFalse(self.out.exists())

    def test_cli_error_does_not_echo_sensitive_member_name(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('../' + self.token, b'fixture')
        self.write(packet({'bad.zip': data.getvalue()}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = gate.main(['--source', str(self.source), '--out', str(self.out), '--expected-commit', COMMIT])
        self.assertEqual(code, 2)
        self.assertNotIn(self.token, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['reason'], 'UNSAFE_MEMBER_NAME')


if __name__ == '__main__':
    unittest.main()

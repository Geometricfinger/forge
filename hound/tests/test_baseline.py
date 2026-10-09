"""Synthetic tests for detector behavior; none execute inspected target code."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from baseline_sniffers import MAX_SOURCE_BYTES, read_profiles, scan_bytes, scan_file

HERE = Path(__file__).parent
PROFILES = read_profiles(HERE.parent/'profiles'/'baseline.json')
RIGID = PROFILES[0]['id']
UNITS = PROFILES[1]['id']
PATH = PROFILES[2]['id']


def inspect(code, which=RIGID):
    result = scan_bytes(code.encode(), source_id='synthetic://fixture', profiles=PROFILES)
    return [x for x in result['findings'] if x['profile_id'] == which]


def src(call='trimesh.registration.icp(a, b, scale=False, reflection=False)',
        imports='import trimesh', name='f', params='a, b'):
    return f'{imports}\ndef {name}({params}):\n    return {call}\n'


class SnifferTests(unittest.TestCase):
    def test_explicit_rigid_candidate(self):
        self.assertEqual(inspect(src())[0]['status'], 'STATIC_CANDIDATE')

    def test_renamed_function_still_found(self):
        self.assertEqual(inspect(src(name='unhelpful_name'))[0]['status'], 'STATIC_CANDIDATE')

    def test_module_alias(self):
        s = src('tm.registration.icp(a,b,scale=False,reflection=False)', 'import trimesh as tm')
        self.assertEqual(inspect(s)[0]['status'], 'STATIC_CANDIDATE')

    def test_function_import_alias(self):
        s = src('fit(a,b,scale=False,reflection=False)', 'from trimesh.registration import icp as fit')
        self.assertEqual(inspect(s)[0]['status'], 'STATIC_CANDIDATE')

    def test_registration_alias(self):
        s = src('reg.icp(a,b,scale=False,reflection=False)', 'from trimesh import registration as reg')
        self.assertEqual(inspect(s)[0]['status'], 'STATIC_CANDIDATE')

    def test_local_import(self):
        s = 'def f(a,b):\n    from trimesh.registration import icp as fit\n    return fit(a,b,scale=False,reflection=False)\n'
        self.assertEqual(inspect(s)[0]['status'], 'STATIC_CANDIDATE')

    def test_comment_is_not_evidence(self):
        s = '# trimesh.registration.icp(a,b,scale=False,reflection=False)\ndef align_rigid(a,b):\n    return a\n'
        self.assertEqual(inspect(s), [])

    def test_docstring_is_not_evidence(self):
        s = 'def align_rigid(a,b):\n    """trimesh.registration.icp(a,b,scale=False,reflection=False)"""\n    return a\n'
        self.assertEqual(inspect(s), [])

    def test_unrelated_icp_library(self):
        s = src('fake.icp(a,b,scale=False,reflection=False)', 'import fake')
        self.assertEqual(inspect(s), [])

    def test_parameter_shadowing(self):
        self.assertEqual(inspect(src(params='a,b,trimesh')), [])

    def test_local_rebinding(self):
        s = 'import trimesh\ndef f(a,b):\n    trimesh=a\n    return trimesh.registration.icp(a,b,scale=False,reflection=False)\n'
        self.assertEqual(inspect(s), [])

    def test_module_rebinding(self):
        s = 'import trimesh\ntrimesh=object()\n'+src(imports='').lstrip()
        self.assertEqual(inspect(s), [])

    def test_scale_true_is_contradiction(self):
        self.assertEqual(inspect(src().replace('scale=False','scale=True'))[0]['status'], 'CONTRADICTED_CALL_CONTRACT')

    def test_reflection_true_is_contradiction(self):
        self.assertEqual(inspect(src().replace('reflection=False','reflection=True'))[0]['status'], 'CONTRADICTED_CALL_CONTRACT')

    def test_missing_keyword_is_unknown(self):
        self.assertEqual(inspect(src().replace(', reflection=False',''))[0]['status'], 'NEEDS_CONTEXT')

    def test_variable_keyword_is_unknown(self):
        self.assertEqual(inspect(src().replace('scale=False','scale=setting'))[0]['status'], 'NEEDS_CONTEXT')

    def test_expanded_keywords_unknown(self):
        s=src('trimesh.registration.icp(a,b,scale=False,reflection=False,**opts)')
        self.assertEqual(inspect(s)[0]['status'], 'NEEDS_CONTEXT')

    def test_integer_zero_not_boolean_false(self):
        self.assertEqual(inspect(src().replace('scale=False','scale=0'))[0]['status'], 'NEEDS_CONTEXT')

    def test_top_level_only_call_not_a_function(self):
        self.assertEqual(inspect('import trimesh\ntrimesh.registration.icp(a,b,scale=False,reflection=False)'), [])

    def test_nested_function_does_not_attribute_to_parent(self):
        s='def outer():\n    def inner(a,b):\n        import trimesh\n        return trimesh.registration.icp(a,b,scale=False,reflection=False)\n    return inner\n'
        hits=inspect(s)
        self.assertEqual([x['qualified_name'] for x in hits], ['outer.<locals>.inner'])

    def test_nested_shadowing_not_guessed(self):
        s='import trimesh\ndef outer(trimesh):\n    def inner(a,b):\n        return trimesh.registration.icp(a,b,scale=False,reflection=False)\n    return inner\n'
        self.assertEqual(inspect(s), [])

    def test_source_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel=Path(tmp)/'must_not_exist'
            s=f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("unsafe")\n'+src()
            inspect(s)
            self.assertFalse(sentinel.exists())

    def test_returned_records_have_no_implementation_body(self):
        s=src().replace('    return', '    private_marker="NEVER_RETAIN_THIS_PRIVATE_LITERAL"\n    return')
        self.assertNotIn('NEVER_RETAIN_THIS_PRIVATE_LITERAL', json.dumps(inspect(s)))

    def test_source_hash_matches(self):
        s=src()
        self.assertEqual(inspect(s)[0]['source_sha256'], hashlib.sha256(s.encode()).hexdigest())

    def test_deterministic_ids(self):
        self.assertEqual(inspect(src()), inspect(src()))

    def test_changed_source_new_finding(self):
        self.assertNotEqual(inspect(src())[0]['finding_id'], inspect(src()+'\n')[0]['finding_id'])

    def test_versioned_profile_can_change_goal(self):
        p=json.loads(json.dumps(PROFILES[:1]))
        p[0]['version']='2';p[0]['required_keywords']['scale']=True
        code=src().replace('scale=False','scale=True').encode()
        got=scan_bytes(code,source_id='synthetic://x',profiles=p)['findings'][0]
        self.assertEqual(got['status'],'STATIC_CANDIDATE')
        self.assertEqual(got['profile_version'],'2')

    def test_units_and_finite_cooccurrence(self):
        s='import math\ndef f(rows):\n    for row in rows:\n        if row.get("unit") not in allowed:\n            raise ValueError()\n        if not math.isfinite(row["v"]):\n            raise ValueError()\n'
        self.assertEqual(inspect(s, UNITS)[0]['status'], 'STATIC_CANDIDATE')

    def test_units_alone_not_enough(self):
        s='def f(row):\n    if row.get("unit") not in allowed:\n        raise ValueError()\n'
        self.assertEqual(inspect(s,UNITS), [])

    def test_path_guard_candidate(self):
        s='def f(root,parts):\n    candidate=root.joinpath(parts).resolve()\n    if not candidate.is_relative_to(root):\n        raise ValueError()\n    return candidate\n'
        self.assertEqual(inspect(s,PATH)[0]['status'], 'STATIC_CANDIDATE')

    def test_path_check_without_rejection_not_enough(self):
        s='def f(root,parts):\n    candidate=root.joinpath(parts).resolve()\n    return candidate.is_relative_to(root)\n'
        self.assertEqual(inspect(s,PATH), [])

    def test_malformed_source_fails(self):
        with self.assertRaises(SyntaxError): inspect('def broken(')

    def test_oversize_fails(self):
        with self.assertRaises(ValueError):
            scan_bytes(b'#'+b'x'*MAX_SOURCE_BYTES, source_id='test',profiles=PROFILES)

    def test_subprocess_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'test.py';file.write_text(src())
            original=file.read_bytes()
            got=scan_file(file,source_id='synthetic://file',profiles_path=HERE.parent/'profiles'/'baseline.json')
            self.assertTrue(got['source_preserved'])
            self.assertEqual(original,file.read_bytes())

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file.py';file.write_text(src())
            link=Path(tmp)/'link.py';link.symlink_to(file)
            with self.assertRaises(ValueError):
                scan_file(link,source_id='synthetic://link',profiles_path=HERE.parent/'profiles'/'baseline.json')

    def test_no_result_claims_runtime_verified(self):
        self.assertTrue(all(not r['runtime_verified'] for r in inspect(src())))

    def test_unknown_detector_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=json.loads(json.dumps(PROFILES));p[0]['detector']='run_python'
            path=Path(tmp)/'profiles.json';path.write_text(json.dumps(p))
            with self.assertRaises(ValueError):read_profiles(path)


if __name__=='__main__':unittest.main()

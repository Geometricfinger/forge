"""Fixed provider-boundary cases. All source bytes here are first-party fixtures."""
import copy, hashlib, io, json, stat, unittest, zipfile
from forge_core import corpus
from forge_core.common import Blocked, sha

def blob(data): return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def capsule(entries=None):
    entries=entries or [('pkg/a.py',b'def f(x): return x\n')]
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for p,b in entries:z.writestr(p,b)
    data=out.getvalue(); commit='a'*40;repo='example/fixture'
    return data, {'provider':'github','file_id':f'github:{repo}@{commit}:selected-files',
      'path':'fixture.zip','size':len(data),'sha256':sha(data),
      'source_url':f'https://github.com/{repo}/tree/{commit}', 'repository':repo,'commit':commit,
      'container_kind':'LOCAL_SELECTED_FILE_ENVELOPE_NOT_FULL_REPOSITORY_ARCHIVE',
      'selected_paths':[p for p,b in entries],
      'members':[{'path':p,'size':len(b),'sha256':sha(b),'git_blob_sha1':blob(b)} for p,b in entries]}

def validate(row):return corpus.manifest({'schema':1,'files':[row]})
def inspect(data,row):
    from forge_core.github_corpus import validate_payload
    return validate_payload(data,row)

class GitHubCorpusTests(unittest.TestCase):
    def test_valid_manifest(self):
        _,r=capsule();self.assertEqual(validate(r)['files'][0]['provider'],'github')
    def test_valid_payload(self):
        b,r=capsule();self.assertEqual(inspect(b,r)['members_verified'],1)
    def test_exact_blob_recorded(self):
        b,r=capsule();self.assertFalse(inspect(b,r)['upstream_code_executed'])
    def test_commit_required(self):
        _,r=capsule();r['commit']='main'
        with self.assertRaises(Blocked):validate(r)
    def test_repository_required(self):
        _,r=capsule();r['repository']='../unsafe'
        with self.assertRaises(Blocked):validate(r)
    def test_url_cannot_redirect(self):
        _,r=capsule();r['source_url']='https://example.com/read'
        with self.assertRaises(Blocked):validate(r)
    def test_identity_matches_commit(self):
        _,r=capsule();r['file_id']=r['file_id'].replace('a'*40,'b'*40)
        with self.assertRaises(Blocked):validate(r)
    def test_missing_members(self):
        _,r=capsule();r.pop('members')
        with self.assertRaises(Blocked):validate(r)
    def test_empty_members(self):
        _,r=capsule();r['members']=[];r['selected_paths']=[]
        with self.assertRaises(Blocked):validate(r)
    def test_duplicate_members(self):
        _,r=capsule();r['members']*=2;r['selected_paths']*=2
        with self.assertRaises(Blocked):validate(r)
    def test_unsafe_member(self):
        _,r=capsule();r['members'][0]['path']='../a.py';r['selected_paths']=['../a.py']
        with self.assertRaises(Blocked):validate(r)
    def test_member_claim_mismatch(self):
        _,r=capsule();r['selected_paths']=['pkg/b.py']
        with self.assertRaises(Blocked):validate(r)
    def test_bad_blob_type(self):
        _,r=capsule();r['members'][0]['git_blob_sha1']=True
        with self.assertRaises(Blocked):validate(r)
    def test_bad_size_bool(self):
        _,r=capsule();r['members'][0]['size']=True
        with self.assertRaises(Blocked):validate(r)
    def test_non_python_member_rejected(self):
        _,r=capsule([('pkg/a.exe',b'no')])
        with self.assertRaises(Blocked):validate(r)
    def test_changed_envelope_rejected(self):
        b,r=capsule()
        with self.assertRaises(Blocked):inspect(b+b'changed',r)
    def test_changed_member_digest(self):
        b,r=capsule();r['members'][0]['sha256']='0'*64
        with self.assertRaises(Blocked):inspect(b,r)
    def test_changed_member_blob(self):
        b,r=capsule();r['members'][0]['git_blob_sha1']='0'*40
        with self.assertRaises(Blocked):inspect(b,r)
    def test_extra_member(self):
        b,r=capsule([('pkg/a.py',b'def a(): pass\n'),('pkg/b.py',b'def b(): pass\n')]);r['members']=r['members'][:1];r['selected_paths']=r['selected_paths'][:1]
        with self.assertRaises(Blocked):inspect(b,r)
    def test_missing_member(self):
        b,r=capsule();r['members'].append(dict(r['members'][0],path='pkg/b.py'));r['selected_paths'].append('pkg/b.py')
        with self.assertRaises(Blocked):inspect(b,r)
    def test_symlink_member(self):
        _,r=capsule();o=io.BytesIO()
        with zipfile.ZipFile(o,'w') as z:
            e=zipfile.ZipInfo('pkg/a.py');e.create_system=3;e.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(e,b'def f(x): return x\n')
        b=o.getvalue();r['sha256']=sha(b);r['size']=len(b)
        with self.assertRaises(Blocked):inspect(b,r)
    def test_metadata_not_mutated(self):
        b,r=capsule();before=copy.deepcopy(r);validate(r);inspect(b,r);self.assertEqual(before,r)
    def test_cannot_label_complete_repository(self):
        _,r=capsule();r['container_kind']='FULL_REPOSITORY'
        with self.assertRaises(Blocked):validate(r)
    def test_execution_flags_rejected(self):
        _,r=capsule();r['members'][0]['execute']=True
        with self.assertRaises(Blocked):validate(r)

if __name__=='__main__':unittest.main()

"""A public automation sign-off must not exempt arbitrary email or secrets."""
import re
import unittest
from tools import prepublish_check as check

class BotTrailerChecks(unittest.TestCase):
    def setUp(self):
        self.address='support'+'@'+'github.com'
        self.trailer='Signed-off-by: dependabot[bot] <'+self.address+'>'
        self.patterns={k:re.compile(v) for k,v in check.BUILTIN.items()}
        self.allow=check.load_allowlist()
    def scan(self,text,path='git:commit-metadata'):
        out=[]
        check.scan_text(path,text,self.patterns,self.allow,out,path)
        return [r['rule'] for r in out]
    def test_exact_public_bot_trailer_in_metadata(self):
        self.assertEqual(self.scan(self.trailer),[])
    def test_bare_address_is_not_exempt(self):
        self.assertEqual(self.scan(self.address),['email'])
    def test_trailer_in_source_file_is_not_exempt(self):
        self.assertEqual(self.scan(self.trailer,'notes.md'),['email'])
    def test_adjacent_and_longer_addresses_still_fail(self):
        other='private-contact'+'@'+'mail.dev'
        self.assertEqual(self.scan(self.trailer+' '+other),['email'])
        self.assertEqual(self.scan(self.trailer.replace(self.address,'extra.'+self.address)),['email'])
    def test_adjacent_secret_still_fails(self):
        self.assertEqual(self.scan(self.trailer+' api_'+'key='+repr('synthetic-test-value')),['assigned_secret'])

if __name__=='__main__':unittest.main()

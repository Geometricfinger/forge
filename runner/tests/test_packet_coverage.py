"""Frozen output-review checks: avoid concealing later questions behind early detail."""
import unittest
from test_contract import row
from research_runner.core import compact_packet,canonical

def report():
    return {'objective':'Compare evidence without hiding later outcomes.','queries':[
        {'id':f'q{i}','query':'callback state','status':'PARTIAL_QUERY_ONLY','results':[
            dict(row(f'c{i}_{j}'),snippet='x'*6000) for j in range(5)],'references':[]}
        for i in range(24)]}

class PacketCoverage(unittest.TestCase):
    def test_all_short_query_summaries_before_details(self):
        p=compact_packet(report(),12000)
        self.assertEqual([q['id'] for q in p['queries']],[f'q{i}' for i in range(24)])
    def test_first_evidence_round_reaches_every_query(self):
        p=compact_packet(report(),16000)
        self.assertTrue(all(q['candidates'] for q in p['queries']));self.assertEqual(len(p['queries']),24)
    def test_omissions_accounted_separately(self):
        p=compact_packet(report(),4096)
        self.assertEqual(p['total_questions'],24)
        self.assertEqual(p['omitted_queries']+len(p['queries']),24)
        included=sum(len(q['candidates'])+len(q['references']) for q in p['queries'])
        self.assertEqual(p['omitted_evidence']+included,120)
    def test_no_payload_limit_relaxed(self):
        for limit in [4096,8000,12000,16000]:
            self.assertLessEqual(len(canonical(compact_packet(report(),limit))),limit)
    def test_status_totals_survive_truncation(self):
        p=compact_packet(report(),4096)
        self.assertEqual(p['outcome_counts']['PARTIAL_QUERY_ONLY'],24)
if __name__=='__main__':unittest.main()

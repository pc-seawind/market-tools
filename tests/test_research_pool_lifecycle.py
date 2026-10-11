"""Synthetic-only stdlib suite, also executable on isolated owned CPU hosts."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from mt1.research_pool import apply, snapshot, db_path, research_queues, project, report
from mt1.scope import load, filter_plans


class ResearchPool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'scope.json'
        self.now = datetime.now(timezone.utc)
        self.data = {'scope_epoch': 'synthetic-reset', 'reset_at': (self.now-timedelta(days=10)).isoformat(),
                     'confirmed_holdings': [{'code': f'{n:06}.SZ'} for n in range(9)], 'active_candidates': []}
        self.scope.write_text(json.dumps(self.data))
        self.before = self.scope.read_bytes()
        src = self.root / 'source'; src.write_text('SYNTHETIC COMPANY EVIDENCE — never production')
        self.e = {'scope_epoch': self.data['scope_epoch'], 'scope_sha256': hashlib.sha256(self.before).hexdigest(),
                  'domain': 'investment', 'reviewer': 'synthetic-test', 'code': '999999.SZ', 'state': 'watch',
                  'reason': 'synthetic thesis', 'request_id': 'test1', 'expected_version': 0,
                  'reviewed_at': self.now.isoformat(), 'business_logic': 'synthetic', 'risk': 'synthetic risk',
                  'next_review': (self.now+timedelta(days=2)).isoformat(),
                  'milestones': [{'at': (self.now+timedelta(days=40)).isoformat(), 'description': 'synthetic node'}],
                  'sources': [{'url': 'https://example.invalid/test', 'path': str(src),
                               'sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
                               'published_at': (self.now-timedelta(days=1)).isoformat(), 'fetched_at': self.now.isoformat()}]}

    def put(self, **updates):
        e = {**copy.deepcopy(self.e), **updates}
        return apply(self.scope, e, self.now)

    def test_nine_holdings_zero_candidates_still_research_outside(self):
        records = [{'code': f'{n:06}.SZ', 'channel_risk': {'TREND': {'status': 'unknown'}}} for n in range(20)]
        backlog, batch, held = research_queues(records, records, load(self.scope))
        self.assertEqual((len(backlog), len(batch), len(held)), (11, 10, 9))
        self.assertFalse(set(r['code'] for r in batch) & set(load(self.scope)['holdings']))
        self.assertEqual(filter_plans([{'code': batch[0]['code']}], load(self.scope)), [])

    def test_admit_retry_cas_immutable_scope_and_plan_binding(self):
        r = self.put()
        self.assertEqual(self.put(), r)
        self.assertEqual(len(snapshot(self.scope, 'synthetic-reset')['events']), 1)
        scope = load(self.scope)
        self.assertEqual(len(scope['holdings']), 9)
        self.assertEqual(self.scope.read_bytes(), self.before)
        self.assertFalse(filter_plans([{'code': self.e['code']}], scope))
        p = {'code': self.e['code'], 'scope_epoch': 'synthetic-reset', 'research_event_id': 'test1'}
        self.assertEqual(filter_plans([p], scope)[0]['holding_status'], 'not_held')
        with self.assertRaises(ValueError): self.put(request_id='test2')
        with self.assertRaises(ValueError): self.put(reason='changed')
        with sqlite3.connect(db_path(self.scope)) as con:
            with self.assertRaises(sqlite3.IntegrityError): con.execute('DELETE FROM events')

    def test_upgrade_downgrade_exit_reentry_and_old_plan(self):
        self.put()
        sig = {'status': 'pass', 'reviewer': 'synthetic', 'at': self.now.isoformat()}
        self.put(request_id='up', expected_version=1, state='recommended', valuation='scenario', risk_signoff=sig, valuation_signoff=sig)
        self.assertIn(self.e['code'], load(self.scope)['recommendations'])
        self.put(request_id='down', expected_version=2)
        self.assertIn(self.e['code'], load(self.scope)['candidates'])
        self.put(request_id='out', expected_version=3, state='exited', gap_or_exit_basis='verified thesis invalidation')
        self.assertNotIn(self.e['code'], load(self.scope)['candidates'])
        with self.assertRaises(ValueError): self.put(request_id='reentry', expected_version=4)
        later = self.now+timedelta(seconds=1)
        e = {**self.e, 'request_id': 'new-research', 'expected_version': 4, 'reviewed_at': later.isoformat()}
        apply(self.scope, e, later)
        self.assertFalse(filter_plans([{'code': self.e['code'], 'scope_epoch': 'synthetic-reset', 'research_event_id': 'test1'}], load(self.scope)))
        self.assertEqual(len(snapshot(self.scope, 'synthetic-reset')['events']), 5)
        self.assertFalse(snapshot(self.scope, 'synthetic-reset')['rows'][0]['final_buy'])

    def test_expiry_and_reset_hide_without_destroying_history(self):
        self.put()
        s = load(self.scope)
        project(s, self.scope, self.now+timedelta(days=3))
        self.assertFalse(s['candidates'])
        self.data['scope_epoch'] = 'new-reset'
        self.scope.write_text(json.dumps(self.data))
        self.assertFalse(load(self.scope)['candidates'])
        self.assertEqual(len(snapshot(self.scope, 'synthetic-reset')['events']), 1)
        with self.assertRaises(ValueError): self.put()

    def test_missing_stale_future_bad_hash_and_recommendation_signoff(self):
        for update in ({'sources': []}, {'business_logic': ''}, {'milestones': []},
                       {'state': 'recommended'}, {'reviewed_at': (self.now+timedelta(days=1)).isoformat()},
                       {'reviewed_at': (self.now-timedelta(days=8)).isoformat()}, {'scope_epoch': 'old'}):
            with self.subTest(update=update), self.assertRaises(ValueError): self.put(**update)
        e = copy.deepcopy(self.e); e['sources'][0]['sha256'] = 'wrong'
        with self.assertRaises(ValueError): apply(self.scope, e, self.now)

    def test_unknown_not_rejected_and_pending_explicit(self):
        with self.assertRaises(ValueError): self.put(state='rejected', gap_or_exit_basis='source blocked')
        self.put(state='pending', gap_or_exit_basis='source blocked', research_outcome='data_blocked')
        self.assertFalse(load(self.scope)['candidates'])
        self.assertEqual(snapshot(self.scope, 'synthetic-reset')['counts']['pending'], 1)

    def test_capacity(self):
        with patch('mt1.research_pool.LIMITS', {'watch': 0}):
            with self.assertRaises(ValueError): self.put()

    def test_chinese_report_is_not_completion_claim(self):
        text = report(load(self.scope))
        for phrase in ('实际持仓复核', '持仓外机会', '升级/降级/退出/维持', '故障', '尚无新研究事件', '不是 BUY'):
            self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()

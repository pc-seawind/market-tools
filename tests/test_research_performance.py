"""Independent stdlib arithmetic/data-boundary suite; SYNTHETIC fixtures only."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from mt1.research_performance import evaluate, render, validate_contract


class Performance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        self.contract={'anchor_date':'2026-09-01','target_dates':['2026-09-30','2026-11-02'],
                       'benchmark_code':'BENCH','currency':'CNY','basis':'unadjusted_close',
                       'anchor_policy':'close_observation_not_execution',
                       'fee_scenario':'SYNTHETIC 10bps each way','fees':{'entry_bps':10,'exit_bps':10}}
        rec={'request_id':'rec1','reviewed_at':'2026-09-01T01:00:00+00:00','performance_contract':self.contract}
        self.ep={'episode_id':'episode1','scope_epoch':'test-reset','code':'TEST',
                 'recommendation':rec,'exit':None}
        self.pool={'scope_epoch':'test-reset','episodes':[self.ep]}
        self.stock={'code':'TEST','currency':'CNY','basis':'unadjusted_close','kind':'stock',
                    'rows':[{'date':d,'close':p,'status':'traded'} for d,p in [('2026-09-01',100),('2026-09-30',110),('2026-10-09',120)]]}
        self.index={'code':'BENCH','currency':'CNY','basis':'unadjusted_close','kind':'index','index_type':'price_index',
                    'rows':[{'date':d,'close':p,'status':'traded'} for d,p in [('2026-09-01',200),('2026-09-30',210),('2026-10-09',220)]]}
        self.actions={'code':'TEST','status':'verified','from':'2026-09-01','through':'2026-10-09','events':[]}
        self.bundle={'scope_epoch':'test-reset','asof':'2026-10-09',
                     'series':[{'code':'TEST','source':self.save('stock',self.stock)},
                               {'code':'BENCH','source':self.save('index',self.index)}],
                     'corporate_actions':[{'code':'TEST','source':self.save('actions',self.actions)}]}

    def save(self,name,value):
        path=self.root/name;path.write_text(json.dumps(value))
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'url':'https://example.invalid/SYNTHETIC/'+name,'fetched_at':self.now.isoformat()}

    def result(self):
        return evaluate(self.pool,self.bundle,self.now)

    def test_arithmetic_costs_benchmark_and_immaturity(self):
        before={p:p.read_bytes() for p in self.root.iterdir()}
        r=self.result();x,y=r['results']
        self.assertEqual(x['status'],'verified_price_scenario')
        self.assertAlmostEqual(x['gross_return'],.10)
        self.assertAlmostEqual(x['net_return'],110*.999/(100*1.001)-1)
        self.assertAlmostEqual(x['excess_net_return'],(110/100-210/200)*.999/1.001)
        self.assertEqual(y['status'],'not_matured');self.assertIsNone(y['net_return'])
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.iterdir()})
        self.assertFalse(r['discovery_cohort_used'])
        text=render(r)
        for word in ('10.00%','9.78%','BENCH','10 bps','not_matured','非真实成交','episode1'):
            self.assertIn(word,text)

    def test_unknown_contract_is_not_backfilled_from_observations(self):
        self.ep['recommendation'].pop('performance_contract')
        r=self.result()['results'][0]
        self.assertEqual(r['status'],'unknown');self.assertIsNone(r['net_return'])

    def test_no_bundle_blocks_mature_only(self):
        r=evaluate(self.pool,None,self.now)['results']
        self.assertEqual([v['status'] for v in r],['blocked','not_matured'])

    def test_mature_missing_date_no_nearest_price(self):
        self.stock['rows'].pop(1)
        self.bundle['series'][0]['source']=self.save('stock2',self.stock)
        r=self.result()['results'][0]
        self.assertEqual(r['status'],'blocked');self.assertIsNone(r['net_return'])

    def test_suspension_and_delisting_block(self):
        for status in ('suspended','delisted'):
            with self.subTest(status=status):
                self.stock['rows'][1]['status']=status
                self.bundle['series'][0]['source']=self.save('stock2',self.stock)
                self.assertEqual(self.result()['results'][0]['status'],'blocked')

    def test_corporate_actions_must_be_explicitly_verified(self):
        for bad in (None,'split','dividend','merger'):
            with self.subTest(action=bad):
                self.bundle['corporate_actions']=[] if bad is None else [{'code':'TEST','source':self.save('actions2',{**self.actions,'events':[{'date':'2026-09-05','type':bad}]})}]
                r=self.result()['results'][0]
                self.assertEqual(r['status'],'blocked');self.assertIsNone(r['net_return'])

    def test_action_coverage_hole(self):
        self.actions['through']='2026-09-20'
        self.bundle['corporate_actions'][0]['source']=self.save('actions2',self.actions)
        self.assertEqual(self.result()['results'][0]['status'],'blocked')

    def test_hash_epoch_basis_currency_and_future_data(self):
        original=copy.deepcopy(self.bundle)
        for bad in ('hash','epoch','basis','currency','future','duplicate','NaN'):
            with self.subTest(bad=bad):
                self.bundle=copy.deepcopy(original);data=copy.deepcopy(self.stock)
                if bad=='hash': self.bundle['series'][0]['source']['sha256']='bad'
                elif bad=='epoch':self.bundle['scope_epoch']='old-reset'
                else:
                    if bad=='basis':data['basis']='qfq'
                    if bad=='currency':data['currency']='HKD'
                    if bad=='future':data['rows'].append({'date':'2026-10-12','close':1,'status':'traded'})
                    if bad=='duplicate':data['rows'].append(data['rows'][0])
                    if bad=='NaN':data['rows'][1]['close']='NaN'
                    self.bundle['series'][0]['source']=self.save('stock2',data)
                r=self.result()['results'][0]
                self.assertEqual(r['status'],'blocked');self.assertIsNone(r['net_return'])

    def test_stale_bundle_is_not_immature(self):
        self.bundle['asof']='2026-09-20'
        r=self.result()['results'][0]
        self.assertEqual(r['status'],'blocked')
        self.assertIn('matured',r['reason'])

    def test_exit_and_reentry_separate_old_exit_from_new_recommendation(self):
        exit_contract={**self.contract,'anchor_date':'2026-09-30','target_dates':['2026-10-09']}
        self.ep['exit']={'request_id':'exit1','reviewed_at':'2026-09-30T10:00:00+00:00','performance_exit':exit_contract}
        second={'episode_id':'episode2','scope_epoch':'test-reset','code':'TEST',
                'recommendation':{'request_id':'rec2','reviewed_at':'2026-10-09T10:00:00+00:00',
                                  'performance_contract':{**self.contract,'anchor_date':'2026-10-09','target_dates':['2026-11-09']}},'exit':None}
        self.pool['episodes'].append(second)
        r=self.result();rows=r['results']
        exit_row=next(v for v in rows if v['kind']=='post_exit_counterfactual')
        self.assertEqual(exit_row['episode_id'],'episode1')
        self.assertAlmostEqual(exit_row['gross_return'],120/110-1)
        self.assertTrue(any(v['kind']=='recommendation_to_exit' and v['status']=='verified_price_scenario' for v in rows))
        self.assertTrue(any(v['status']=='closed_before_horizon' for v in rows))
        self.assertTrue(any(v['episode_id']=='episode2' and v['status']=='not_matured' for v in rows))
        self.assertIn('退出后反事实',render(r))

    def test_unknown_exit_date_does_not_assume_continued_hold(self):
        self.ep['exit']={'request_id':'exit','reviewed_at':'2026-09-30T10:00:00+00:00'}
        self.assertTrue(all(r['net_return'] is None for r in self.result()['results']))

    def test_original_baseline_date_cannot_be_backdated(self):
        with self.assertRaises(ValueError):
            validate_contract(self.contract,datetime(2026,9,2,tzinfo=timezone.utc))

    def test_reset_never_includes_old_episode(self):
        self.pool['scope_epoch']='new-reset'
        self.assertEqual(self.result()['results'],[])

    def test_intraday_target_not_matured(self):
        self.contract['target_dates']=['2026-10-11']
        early=datetime(2026,10,11,2,tzinfo=timezone.utc)
        row=evaluate(self.pool,None,early)['results'][0]
        self.assertEqual(row['status'],'not_matured')
        self.assertIsNone(row['net_return'])

    def test_intraday_capture_cannot_prove_end_of_day_close(self):
        self.bundle['series'][0]['source']['fetched_at']='2026-10-09T01:00:00+00:00'
        row=self.result()['results'][0]
        self.assertEqual(row['status'],'blocked')
        self.assertIn('before conservative',row['reason'])


    def test_malformed_materials_report_blocked_not_crash_or_profit(self):
        original=copy.deepcopy(self.bundle)
        for bad in ('series_list','source_array','rows','actions_list','future_action_coverage'):
            with self.subTest(bad=bad):
                self.bundle=copy.deepcopy(original)
                if bad=='series_list': self.bundle['series']=[None]
                if bad=='source_array': self.bundle['series'][0]['source']=self.save('bad',[])
                if bad=='rows': self.bundle['series'][0]['source']=self.save('bad',{**self.stock,'rows':[None]})
                if bad=='actions_list': self.bundle['corporate_actions']=[None]
                if bad=='future_action_coverage': self.bundle['corporate_actions'][0]['source']=self.save('bad',{**self.actions,'through':'2099-01-01'})
                result=self.result()
                self.assertEqual(result['results'][0]['status'],'blocked')
                self.assertIsNone(result['results'][0]['net_return'])
                self.assertIn('blocked',render(result))
        self.contract['fees']=None
        self.assertIn('unknown',render(self.result()))



if __name__=='__main__':unittest.main()

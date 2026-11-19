from datetime import datetime
import json
import pytest
from pathlib import Path

import investment_cron as cron
from scripts.prepare_investment_cron import compact, prepare, units


def test_prospective_plan_moves_only_direct_slots():
    before = {'name': 'daily-rec-verify', 'cron': '0 18 * * 1-5', 'enabled': True,
              'delivery': 'feishu:oc_test', 'prompt': 'original'}
    stage = prepare([before], datetime(2026, 11, 20, 17, tzinfo=cron.TZ), 'scope', days=3)
    after = stage['changes'][0]['after']
    assert after['enabled'] is False and after['delivery'] == before['delivery']
    row, = stage['plan']['expected']
    assert row['execution_id'] == 'cron:daily-rec-verify:20261120T1800'
    assert row['job'] == 'daily-track' and row['run_id'] == 'daily-rec-verify-20261120T1800'
    assert 'OnCalendar=Mon..Fri 18:00:00 Asia/Shanghai' in dict(units(before['delivery']))[
        'investment-cron-daily-rec-verify.timer']


def test_compact_preserves_radar_judgment_and_routes_artifact():
    p = '[EXPECTED_REVISION_3]\nold\n[/EXPECTED_REVISION_3]\n\n1. 读取今日 prefilter handoff：old.\n2. 继续核验来源。'
    s = compact('narrative-radar-daily', p)
    assert 'old' not in s and 'registered/' in s and '继续核验来源' in s


def test_normal_scheduled_run_and_duplicate_dont_notify(tmp_path, monkeypatch):
    monkeypatch.setattr(cron, 'ROOT', tmp_path)
    scope = tmp_path / 'scope.json'
    scope.write_text('{"scope_epoch":"x"}')
    monkeypatch.setattr(cron, 'SCOPE', scope)
    monkeypatch.setattr(cron, 'now', lambda: datetime(2026, 11, 20, 19, 45, 2, tzinfo=cron.TZ))
    calls = []
    monkeypatch.setattr(cron, 'narrative_track', lambda *args: (calls.append(args) or {'status':'ok'}, [], []))
    monkeypatch.setattr(cron, 'archive', lambda **kw: {'manifest': kw['execution']['execution_id']})
    monkeypatch.setattr(cron, 'notify', lambda *args: pytest.fail('push on normal run'))
    assert cron.run('narrative-track-daily', 'chat') == 0
    assert cron.run('narrative-track-daily', 'chat') == 0
    assert len(calls) == 1
    assert cron.load(cron.handoff('narrative-track-daily', '2026-11-20'))['archive'][
        'manifest'] == 'cron:narrative-track-daily:20261120T1945'


def test_narrative_retry_same_explicit_day(tmp_path, monkeypatch):
    def execute(args, directory, label, timeout):
        (directory / (label + '.log')).write_text('fixture')
        assert args[3:5] == ['--date', '20261120']
        Path(args[-1]).write_text(json.dumps({'fetch_failed': int(label.endswith('1'))}))
        return 0
    monkeypatch.setattr(cron, 'execute', execute)
    r, _, _ = cron.narrative_track(tmp_path, '2026-11-20')
    assert r['status'] == 'ok' and r['attempts'] == 2


def test_report_reader_surface_confirmed_result_not_raw(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cron, 'ROOT', tmp_path)
    raw = tmp_path / 'actual.json'
    raw.write_text(json.dumps({'trade_date':'2026-11-20','scope_epoch':'scope','status':'partial',
                              'rows':[{'ticker':'X','close':7,'raw':'large'}]}))
    cron.register_thesis(raw)
    cron.brief('2026-11-20')
    result = json.loads(capsys.readouterr().out)
    assert result['thesis-enrich-daily']['rows'] == [{'ticker':'X','close':7}]
    assert result['daily-rec-verify']['status'] == 'missing'


def test_gateway_compare_before_write_and_preserve_delivery(tmp_path):
    from scripts.apply_investment_cron_gateway import apply
    p = tmp_path / 'task.json'
    before = {'name':'task','enabled':True,'domain':'investment','prompt':'old','delivery':'feishu:oc_test'}
    p.write_text(json.dumps(before))
    bad = {'name':'task','before':{**before,'prompt':'different'},'after':before}
    with pytest.raises(ValueError, match='concurrent'):
        apply(tmp_path, {'changes':[bad]})
    assert cron.load(p) == before
    after = {**before, 'prompt':'short'}
    swapped = apply(tmp_path, {'changes':[{'name':'task','before':before,'after':after}]})
    assert cron.load(p) == after
    assert cron.load(Path(swapped['backup']) / p.name) == before


def test_prefilter_archives_breaking_and_keeps_seed_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(cron, 'ROOT', tmp_path)
    def execute(args, directory, label, timeout):
        log = directory / (label + '.log')
        if label == 'prefilter':
            Path(args[-1]).write_text('{"title":"lead","is_breaking":true,"relevance":7}\n')
            log.write_text('fixture\n[FAIL] cls\n')
            return 1
        log.write_text('{"ok":true,"text":"not original source"}')
        return 0
    monkeypatch.setattr(cron, 'execute', execute)
    r, materials, alerts = cron.prefilter(tmp_path, '2026-11-20')
    assert r['status'] == 'partial' and r['errors'] == ['cls']
    assert len(cron.records(Path(r['handoff']))) == 1 and r['htsc_seed']
    assert 'lead' in ' '.join(alerts) and Path(r['htsc_seed']) in materials


def test_daily_track_preserves_actual_coverage_and_archive(tmp_path, monkeypatch):
    def execute(args, directory, label, timeout):
        assert args[-4:] == ['--run-id','run','--execution-id','id']
        Path(args[args.index('--out')+1]).write_text(json.dumps({
            'status':'partial','rows':[{}],'failed':[{'code':'X','error':'timeout'}],
            'archive':{'manifest':'actual'},
        }))
        return 75
    monkeypatch.setattr(cron, 'execute', execute)
    r, _, _ = cron.daily_track(tmp_path, '2026-11-20', 'run', 'id')
    assert r['status'] == 'partial' and r['archive']['manifest'] == 'actual'
    assert r['errors'] == ['X: timeout']

import json
from pathlib import Path
import pytest
from mt1.ta_workflow import evidence_inbox, report_inbox
from mt1.timing_cli import file_hash


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(root):
    raw = root / 'raw'; raw.write_text('original public source')
    text = root / 'text'; text.write_text('verified body')
    evidence = {'evidence_id': 'e_one', 'raw_path': str(raw), 'raw_sha256': file_hash(raw),
                'text_path': str(text), 'text_sha256': file_hash(text)}
    stock = {'code': '001309.SZ', 'evidence': [evidence], 'coverage': {'status': 'blocked'}}
    parent = root / 'parent.json'; write(parent, {'stocks': [stock]})
    current = root / 'collection.json'
    write(current, {'stocks': [stock], 'parent_collection_hash': file_hash(parent)})
    pointer = root / 'evidence-supplements' / 'one.json'
    write(pointer, {'collection': str(current), 'sha256': file_hash(current),
                    'parent_collection': str(parent), 'parent_sha256': file_hash(parent), 'work_id': 'test'})
    return pointer, current, parent, raw, text


def test_supplement_is_separate_and_read_only(tmp_path):
    pointer, current, parent, raw, text = fixture(tmp_path)
    before = {p: p.read_bytes() for p in [pointer, current, parent, raw, text]}
    row = evidence_inbox(tmp_path)[0]
    assert row['status'] == 'pending_independent_source_review'
    assert row['not_published'] and row['does_not_replace_frozen_model_evidence']
    assert row['stocks'][0]['research_coverage']['status'] == 'blocked'
    assert before == {p: p.read_bytes() for p in before}
    assert not (tmp_path / 'latest.json').exists()
    result = report_inbox(tmp_path / 'consumer', root=tmp_path)
    data = json.loads(Path(result['path']).read_text())
    assert not data['items'] and data['evidence_supplements'] == [row]


@pytest.mark.parametrize('target', ['current', 'parent', 'raw', 'text'])
def test_tampering_quarantined(tmp_path, target):
    pointer, current, parent, raw, text = fixture(tmp_path)
    locals()[target].write_text('{}')
    assert evidence_inbox(tmp_path)[0]['status'] == 'blocked_invalid_supplement'


@pytest.mark.parametrize('codes', [[], ['OTHER'], ['001309.SZ', '001309.SZ']])
def test_scope_cannot_expand_or_drop(tmp_path, codes):
    pointer, current, *_ = fixture(tmp_path)
    data = json.loads(current.read_text()); stock = data['stocks'][0]
    data['stocks'] = [{**stock, 'code': code} for code in codes]
    write(current, data)
    p = json.loads(pointer.read_text()); p['sha256'] = file_hash(current); write(pointer, p)
    assert evidence_inbox(tmp_path)[0]['error'] == 'supplement_scope_mismatch'


def test_broken_pointer_does_not_hide_good_supplement(tmp_path):
    fixture(tmp_path)
    write(tmp_path / 'evidence-supplements' / 'broken.json', {})
    assert {r['status'] for r in evidence_inbox(tmp_path)} == {
        'blocked_invalid_supplement', 'pending_independent_source_review'}


@pytest.mark.parametrize('valid',[True,False])
def test_only_valid_child_supersedes_old_review(tmp_path,valid):
    pointer,current,*_=fixture(tmp_path)
    p=json.loads(pointer.read_text());child=tmp_path/'child.json'
    data=json.loads(current.read_text());data['parent_collection_hash']=file_hash(current)
    write(child,data)
    write(tmp_path/'evidence-supplements'/'child.json',{
        **p,'collection':str(child),'sha256':file_hash(child) if valid else 'bad',
        'parent_collection':str(current),'parent_sha256':file_hash(current)})
    rows=evidence_inbox(tmp_path)
    old=next(r for r in rows if r.get('sha256')==file_hash(current))
    assert old['status']==('superseded_by_source_revision' if valid else 'pending_independent_source_review')

#!/usr/bin/env python3
"""Adapt an investment-domain partial note WITHOUT promoting it to signed research."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path


def convert(source):
    source=Path(source);raw=source.read_bytes();note=json.loads(raw)
    now=datetime.now(timezone.utc).isoformat();result=[]
    for item in note['items']:
        hypothesis=item['channel_hypothesis']
        channels=[c for c in ('VALUE','TREND','REVERSAL') if c in hypothesis]
        for ch in channels:
            result.append(dict(code=item['code'],channel=ch,kind='evidence',
                reviewer='code_structural_import_of_investment_partial_note_not_company_signoff',
                reviewed_at=now,valid_until=note['review_due'],conclusion='partial',
                reason=item['research_inference'],facts=item['facts'],remaining_checks=item['next_checks'],
                imported_at=now,original_research_at=note['fetched_at'],
                sources=[dict(url=item['source_url'],published_at=note['fetched_at'],
                    filing_publication_date=item['publication_date'],path=str(source.resolve()),sha256=hashlib.sha256(raw).hexdigest(),
                    source_type='investment_secondary_research_note_not_primary_filing',
                    verification_limit=note['sources_caveat'])]))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    with Path(a.out).open('x') as f:json.dump(convert(a.source),f,ensure_ascii=False,indent=2)

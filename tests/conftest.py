"""Opt-in filesystem isolation; production hashing/validation stays real."""
from pathlib import Path
import pytest


@pytest.fixture
def isolated_protected_repository(tmp_path, monkeypatch):
    """Do not scan the operator's growing archive in synthetic lifecycle tests.

    Only the filesystem root moves. protected_hashes, archive integrity, state
    transitions, fsync and every test horizon/iteration remain untouched.
    Nonempty fixtures make the protection checks meaningful rather than {} == {}.
    """
    import mt1.timing_cli as cli
    root = tmp_path / 'protected-repository'
    for rel in ('watchlist.yaml', 'watchlist_changes.jsonl', 'recommendations.jsonl',
                'watchlist/nested/example.yaml', '.cron_state/mt1/plans.db',
                '.cron_state/mt1/nested/forward-cohort.json'):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('SYNTHETIC_PROTECTED_FILE\n')
    monkeypatch.setattr(cli, 'HERE', root)
    return root

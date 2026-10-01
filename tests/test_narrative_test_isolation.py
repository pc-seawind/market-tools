"""The long-test fixture must not disable protection or change real source roots."""
from pathlib import Path
import mt1.timing_cli as cli


def test_isolated_protection_enumerates_and_detects_mutation(tmp_path, isolated_protected_repository):
    scope = tmp_path/'scope.json'; scope.write_text('{"synthetic":true}')
    root = isolated_protected_repository
    before = cli.protected_hashes(scope)
    expected = {str(p.resolve()) for p in root.rglob('*') if p.is_file()} | {str(scope.resolve())}
    assert set(before) == expected and len(before) == 7
    assert str(Path(cli.__file__).resolve()).startswith(str(Path(__file__).resolve().parents[1]))
    target = root/'.cron_state/mt1/nested/forward-cohort.json'
    target.write_text('SYNTHETIC_MUTATION\n')
    after = cli.protected_hashes(scope)
    assert before != after
    assert {p for p in before if before[p] != after[p]} == {str(target.resolve())}

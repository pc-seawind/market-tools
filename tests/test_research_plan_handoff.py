from scripts.verify_research_plan_handoff import verify


def test_real_cli_formal_watch_handoff(tmp_path):
    verify(tmp_path/'isolated')

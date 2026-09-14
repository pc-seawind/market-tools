import narrative_radar as nr

def test_empty_picker_prints_explicit_report(monkeypatch, capsys):
    monkeypatch.setattr(nr, "load_events", lambda: [])
    nr.cmd_picker_doc(None)
    output = capsys.readouterr().out
    assert "0 条" in output
    assert "本周无 score≥2 事件" in output

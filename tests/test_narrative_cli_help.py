"""CLI help must render literal percentage units without executing business work."""
import subprocess
import sys
from pathlib import Path


def test_add_help_renders_percentage():
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "narrative_radar.py"), "add", "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "收益率(%)" in result.stdout

"""MT entry points must work in detached safe-path environments, from any cwd."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["mt1.py", "mt1_job.py"])
@pytest.mark.parametrize("safe_mode", ["default", "env", "flag"])
def test_cli_help_from_foreign_cwd(tmp_path, script, safe_mode):
    env = dict(os.environ)
    env.pop("PYTHONSAFEPATH", None)
    # Match the worker's unrelated PYTHONPATH; never inherit a test-only repo path.
    env["PYTHONPATH"] = str(tmp_path / "worker-source")
    env["TUSHARE_TOKEN"] = "synthetic-help-only-no-network"
    if safe_mode == "env":
        env["PYTHONSAFEPATH"] = "1"
    flags = ["-P"] if safe_mode == "flag" else []
    result = subprocess.run(
        [sys.executable, *flags, str(ROOT / script), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "research-status" in result.stdout
    assert "archive-evidence" in result.stdout
    assert not list(tmp_path.glob(".cron_state*"))


def test_launcher_passes_explicit_package_path_without_disabling_safe_path(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mt1_job_entrypoint_test", ROOT / "mt1_job.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-help-only-no-network")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    worker_source = str(tmp_path / "worker-source")
    monkeypatch.setenv("PYTHONPATH", worker_source)
    monkeypatch.setattr(sys, "argv", [str(ROOT / "mt1_job.py"), "--help"])
    captured = {}

    def execve(executable, argv, env):
        captured.update(executable=executable, argv=argv, env=env)

    monkeypatch.setattr(module.os, "execve", execve)
    module.main()
    assert captured["argv"] == [sys.executable, str(ROOT / "mt1.py"), "--help"]
    assert captured["env"]["PYTHONPATH"].split(os.pathsep) == [str(ROOT), worker_source]
    assert captured["env"]["PYTHONSAFEPATH"] == "1"

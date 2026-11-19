"""Run the three investment producers without a model; register handoffs by day."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from zoneinfo import ZoneInfo

from mt1.data import atomic_json
from mt1.longitudinal import archive

HERE = Path(__file__).resolve().parent
WORK = Path.home() / "work/investment"
ROOT = WORK / "reference/background-runs"
SCOPE = WORK / "reference/tracking-scope.json"
TZ = ZoneInfo("Asia/Shanghai")
SLOTS = {
    "narrative-prefilter-daily": ("1700", "0 17 * * *", "17:00:00"),
    "daily-rec-verify": ("1800", "0 18 * * 1-5", "Mon..Fri 18:00:00"),
    "narrative-track-daily": ("1945", "45 19 * * 1-5", "Mon..Fri 19:45:00"),
}


def now():
    return datetime.now(TZ)


def load(path):
    return json.loads(path.read_text())


def handoff(job, day):
    return ROOT / job / "registered" / (day + ".json")


def execute(args, directory, label, timeout):
    with (directory / (label + ".log")).open("wb") as log:
        return subprocess.run(args, cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                              timeout=timeout, check=False).returncode


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def prefilter(directory, day):
    out = directory / "candidates.jsonl"
    rc = execute(["bash", str(HERE / "narrative_prefilter.sh"), "--min-relevance", "3",
                  "--out", str(out)], directory, "prefilter", 300)
    log = directory / "prefilter.log"
    raw_match = re.search(r"^RAW_RESPONSE_DIR=(.+)$", log.read_text(), re.M)
    raw_dir = Path(raw_match[1]) if raw_match else None
    failures = re.findall(r"^\[FAIL\] (\w+)", log.read_text(), re.M)
    materials = [log, *(sorted(raw_dir.glob("*.response.txt")) if raw_dir else [])]
    if rc not in (0, 1, 2):
        return {"status": "failed", "errors": [f"三源预筛退出码 {rc}"]}, materials, []
    rows = records(out)
    if rows:
        materials.append(out)
    # This is a lead source only; it is never merged into verified narrative events.
    seed = directory / "htsc-seed.log"
    try:
        hrc = execute([sys.executable, str(HERE / "htsc_skill_bridge.py"), "narrative-seeds",
                       "--timeout", "120", "--retries", "1", "--cache-ttl", "21600"],
                      directory, "htsc-seed", 260)
        seed_ok = hrc == 0 and load(seed)["ok"]
        seed_error = None if seed_ok else f"HTSC 无可用输出，退出码 {hrc}"
    except (ValueError, subprocess.TimeoutExpired) as exc:
        seed_ok, seed_error = False, str(exc)
    if seed.stat().st_size:
        materials.append(seed)
    breaking = [r for r in rows if r.get("is_breaking") and r["relevance"] >= 7]
    # Only compare to the preceding producer state, never scan historical runs.
    state = ROOT / "narrative-prefilter-daily/registered/last-count.json"
    try:
        previous_count = load(state)["candidate_count"]
    except FileNotFoundError:
        previous_count = None
    result = {"status": "failed" if rc == 2 else "partial" if rc == 1 else "ok",
              "errors": failures, "candidate_count": len(rows),
              "high_breaking_count": len(breaking), "handoff": str(out),
              "htsc_seed": str(seed) if seed_ok else None, "htsc_error": seed_error}
    alerts = ([f"失败源：{'、'.join(failures)}"] if failures else [])
    if not rows and previous_count:
        alerts.append("候选从非空降为 0")
    if breaking:
        alerts.append("高相关快讯：" + "；".join(str(r.get("title", ""))[:85] for r in breaking[:3]))
    if rc != 2:
        atomic_json(state, {"candidate_count": len(rows), "trade_date": day})
    return result, materials, alerts


def narrative_track(directory, day):
    outputs = []
    stats = None
    for attempt in (1, 2):
        path = directory / f"verify-{attempt}.json"
        rc = execute([sys.executable, str(HERE / "mt1_job.py"), "narrative-verify",
                      "--date", day.replace("-", ""), "--result-json", str(path)],
                     directory, f"verify-{attempt}", 240)
        outputs.append(directory / f"verify-{attempt}.log")
        if rc != 0:
            return {"status": "failed", "errors": [f"T+N verify 退出码 {rc}"]}, outputs, []
        stats = load(path)
        outputs.append(path)
        if not stats["fetch_failed"]:
            break
    failures = stats["fetch_failed"]
    return ({"status": "partial" if failures else "ok", "stats": stats, "attempts": attempt,
             "errors": [f"同日复核仍有 {failures} 项行情失败"] if failures else []},
            outputs, [])


def daily_track(directory, day, run_id, execution_id):
    out = directory / "daily-track.json"
    rc = execute([sys.executable, str(HERE / "mt1_job.py"), "--scope", str(SCOPE), "daily-track",
                  "--out", str(out), "--run-id", run_id, "--execution-id", execution_id],
                 directory, "daily-track", 480)
    if rc not in (0, 75):
        return {"status": "failed", "errors": [f"持仓跟踪退出码 {rc}"]}, [], []
    result = load(out)
    errors = [f'{r["code"]}: {r["error"]}' for r in result["failed"]]
    return {"status": result["status"], "errors": errors, "artifact": str(out),
            "archive": result["archive"], "coverage": len(result["rows"])}, [], []


def notify(chat, execution_id, name, alerts):
    note = f"【投资后台｜{name}】\n" + "\n".join(str(a)[:220] for a in alerts[:5])
    args = ["lark-cli", "im", "+messages-send", "--as", "bot", "--chat-id", chat,
            "--idempotency-key", execution_id, "--text", note]
    remote = 'PATH="$HOME/.nvm/versions/node/v24.15.0/bin:$PATH" ' + shlex.join(args)
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                             "vps", remote], capture_output=True, text=True, timeout=18, check=True)
    reply = json.loads(result.stdout)
    return reply["data"]["message_id"]


def run(job, chat):
    started = now()
    day = started.date().isoformat()
    hhmm = SLOTS[job][0]
    scheduled = started.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0)
    if not 0 <= (started - scheduled).total_seconds() < 120:
        raise ValueError("不在自然调度起始窗口；不补造生产执行")
    occurrence = f'{started:%Y%m%d}T{hhmm}'
    execution_id = f"cron:{job}:{occurrence}"
    run_id = f"{job}-{occurrence}"
    directory = ROOT / job / run_id
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        print(f"{job}: 本次运行已被认领，未重复执行")
        try:
            receipt = load(directory / "run.json")
        except FileNotFoundError:
            return 75
        return 0 if receipt["status"] == "ok" else 75
    materials, alerts = [], []
    scope_epoch = None
    try:
        scope_epoch = load(SCOPE)["scope_epoch"]
        if job == "daily-rec-verify":
            result, materials, alerts = daily_track(directory, day, run_id, execution_id)
        elif job == "narrative-prefilter-daily":
            result, materials, alerts = prefilter(directory, day)
        else:
            result, materials, alerts = narrative_track(directory, day)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    result.update(job=job, run_id=run_id, execution_id=execution_id, trade_date=day,
                  started_at=started.isoformat(), completed_at=now().isoformat())
    if scope_epoch:
        result["scope_epoch"] = scope_epoch
        if "archive" not in result:
            try:
                result["archive"] = archive(
                    job=job, run_id=run_id, trade_date=day, scope_epoch=scope_epoch, result=result,
                    materials=[{"name": p.name, "path": str(p)} for p in materials],
                    execution={"execution_id": execution_id, "started_at": result["started_at"],
                               "completed_at": result["completed_at"], "status": result["status"],
                               "source": "systemd worker producer process clock"})
            except (OSError, ValueError) as exc:
                result["status"] = "failed"
                result["errors"].append(f"归档失败: {exc}")
    atomic_json(directory / "run.json", result)
    atomic_json(handoff(job, day), result)
    print(json.dumps(result, ensure_ascii=False))
    if alerts or result["status"] != "ok":
        message_id = notify(chat, execution_id, job, alerts + [
            s for s in result["errors"] if not any(s in alert for alert in alerts)])
        atomic_json(directory / "notification.json", {"message_id": message_id})
    return 0 if result["status"] == "ok" else 75


def register_thesis(path):
    result = load(path)
    if not isinstance(result["rows"], list):
        raise ValueError("thesis summary rows must be a list")
    atomic_json(handoff("thesis-enrich-daily", result["trade_date"]), {
        "artifact": str(path.resolve()), "status": result["status"],
        "scope_epoch": result["scope_epoch"], "trade_date": result["trade_date"]})


def brief(day):
    result = {}
    for job in ("daily-rec-verify", "thesis-enrich-daily"):
        try:
            reg = load(handoff(job, day))
            data = load(Path(reg["artifact"])) if "artifact" in reg else reg
            # Only send the decision-ready view; raw bars and evidence stay at the source.
            fields = ("code", "ticker", "name", "market", "holding_status", "research_pool_state",
                      "quote_date", "close", "pct_today", "day_pct", "kdj", "kdj_status",
                      "condition_status", "technical_status", "pillar_status", "company_review",
                      "source", "fetched_at", "status", "quote_status", "error", "errors")
            rows = data["rows"] if "artifact" in reg else []
            result[job] = {"registration": reg, "rows": [
                {k: r[k] for k in fields if k in r} for r in rows]}
        except FileNotFoundError as exc:
            result[job] = {"status": "missing", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    job = sub.add_parser("run")
    job.add_argument("job", choices=SLOTS)
    job.add_argument("--chat", required=True)
    thesis = sub.add_parser("register-thesis")
    thesis.add_argument("path", type=Path)
    view = sub.add_parser("evening")
    view.add_argument("--date", default=now().date().isoformat())
    a = p.parse_args()
    if a.cmd == "run":
        return run(a.job, a.chat)
    if a.cmd == "register-thesis":
        register_thesis(a.path)
    else:
        brief(a.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())

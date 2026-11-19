"""Prepare the 16-job investment cutover; no live mutations."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from investment_cron import HERE, ROOT, SCOPE, SLOTS, TZ


PLAN = SCOPE.parent / "operations/investment-cron/expected-executions.json"
CLI = f"/usr/bin/python3 {HERE}/investment_cron.py"
MT = f"/usr/bin/python3 {HERE}/mt1_job.py"
REPORTS = {
    "morning-market-brief": "morning",
    "evening-market-recap": "evening",
    "weekend-saturday-recap": "saturday",
    "weekend-sunday-preview": "sunday",
    "weekend-sunday-closeout": "closeout",
}
BLOCKS = ("EXPECTED_REVISION_3", "R3_EXPECTED_EXECUTIONS", "DURABLE_ARCHIVE_CLI_V1",
          "LONGITUDINAL_EVIDENCE_V1", "TRACKING_SCOPE_RESET_V1", "WEEKEND_LOOP_V1",
          "RESEARCH_POOL_LIFECYCLE_V1")
BASE = f"""在既有 home-ubuntu 路由运行。持仓、候选与推荐以当前 {SCOPE} 为准，不写回 scope，不把持仓外研究当正式准入。沿用当前任务的实盘/自选禁写边界。
只记录本轮真实日期、来源及产物，缺失写明；不重跑成功产物，不查历史部署目录、--help、SHA/mtime 或重复校验已登记输入。"""
EVIDENCE = f"""留档：记录本轮实际结果、失败和输入，不将计划时间写为实测；执行 ID 为 cron:<cron 名>:YYYYMMDDTHHMM（北京时间计划分钟），run_id 为 <cron 名>-YYYYMMDDTHHMM。用 {MT} archive-evidence --input <本轮bundle> 写一次回执；已有脚本归档的直接用回执，不重复归档或重算 hash。"""
WEEKLY = f"""本轮周度执行核验只用预登记 {PLAN}；运行 {MT} --scope {SCOPE} weekly-evidence --asof <当前带时区的时间> --expected {PLAN} --out <唯一结果>。这是切换后前瞻清单，其前历史用既有独立归档，保留缺口、不得回填执行；未登记或超出清单覆盖窗口明确未核验。"""
RESEARCH = """研究状态：读本轮 research-status 和已分配任务，旧任务优先、不自行补足名额。发现≠开研≠入池≠正式计划；真正完成才记 research-event，正式计划走 finalize。写入任务状态时才按 market-tools/docs/research-task-disposition.md 的状态契约处理。最终正文展示任务出入账和逾期；周六区分持仓、持仓外原判断核验、退出后表现；周日区分持仓、持仓外机会、池变更。展示原技术信号，不因置信度降级；无成本不算实盘盈亏。"""
ROUTE = """先完成正式正文再工程注记；沿用已登记飞书群与文档入口、留实际发送回执，不伪造链接。"""


def strip_blocks(prompt):
    for block in BLOCKS:
        prompt = re.sub(r"^\[" + block + r"\]\n.*?^\[/" + block + r"\]\s*",
                        "", prompt, flags=re.M | re.S)
    return prompt.strip()


def compact(name, old):
    if name in SLOTS:
        return ("改由 home-ubuntu 的 investment-cron systemd timer 在原时点执行。"
                "本 Gateway 记录仅用于配置回退，不得手动触发重复生产。")
    body = strip_blocks(old)
    body = re.sub(r"(?:本轮|当前|真实|已确认)?9只(?=持仓|ACTIVE|均有档案|原文|确认持仓|的成本|已确认)",
                  "当前范围内", body)
    body = body.replace("周六 7:30 自动触发，在 weekend-saturday-recap 06:00 之后",
                        "周六 07:30 自动触发，消费已完成的日级验证")
    body = body.replace("每周日 10:30", "每周日 04:30")
    body = body.replace("读取 17:00 prefilter 候选 + 搜索源", "消费当日已登记的预筛候选并核验原文")
    if name == "narrative-radar-daily":
        body = re.sub(
            r"(?m)^1\. 读取今日 prefilter handoff：.*$",
            f"1. 读 {ROOT}/narrative-prefilter-daily/registered/<北京时间当日 YYYY-MM-DD>.json；"
            "直接使用 handoff 指向的候选文件。htsc_seed 只读线索；须找到原文才能入库。"
            "未登记或三源失败则如实报缺，不读取前一日代替；正常 0 候选不是失败。",
            body)
    if name in REPORTS:
        phase = REPORTS[name]
        policy = SCOPE.parent / "operations/weekend-loop-policy.md"
        prefix = f"phase={phase}。按 {policy} 只取本阶段的正文、三表和产物结构；其中旧 scope 数量、旧 expected 路径、扫描/验 hash 步骤已由当前规则取代。"
        if phase == "evening":
            body = (f"先运行 `{CLI} evening` 取得当日已登记持仓/推荐行情和 thesis 研判。相同市场、交易日、口径的行直接用于正文，"
                    "只对缺失、实际过晚、不同口径或本阶段需更新的数据做定向补采，保留缺失；预筛、后台核验与研判均不重跑。\n" + body)
        if phase == "closeout":
            body = body.replace("完整检查scope与技术卡覆盖、日期和模型，核对日评timer/journal/最新manifest；",
                                "直接读取已登记的技术卡和后台产物，标明真实覆盖缺口；")
        if phase in ("evening", "morning"):
            prefix += (" 正式报告保留三部分与 MT13 原消费者（如实展示技术动作）；沿用 MT13 REPORT-CONTRACT 的输出契约，"
                       "不重复核验已登记配置。")
        parts = [BASE, prefix, body, RESEARCH]
        if phase in ("saturday", "closeout"):
            parts.append(WEEKLY)
        parts.extend([EVIDENCE, ROUTE])
        return "\n\n".join(parts)
    if name == "thesis-enrich-daily":
        body = re.sub(r"(?m)^2\. 检查 .*$",
                      "2. 使用下面已登记的入口，仅 --dry-run，不写回真实 thesis。", body)
        body += f"\n\n脚本传本轮 --run-id / --execution-id，归档 job=thesis-enrich。本轮 summary JSON 带 trade_date、scope_epoch、status、rows；完成归档后调用 `{CLI} register-thesis <本轮summary绝对路径>`，晚报直接消费，不覆盖原稿。"
    if name == "thesis-weekly-review":
        body = re.sub(r"^【周报索引真实入口】.*\n+", "", body)
        body = re.sub(r"(?s)   先运行 summary 模式:.*?   根据输出决定推送方式:",
                      "   直接用首次运行末尾的异动摘要决定推送方式，不二次运行脚本：", body)
    if name == "thesis-weekly-bootstrap":
        body = body.replace("当前范围内均有档案", "当前范围内所有标的均有档案")
    parts = [BASE, body, EVIDENCE]
    return "\n\n".join(parts)


def prepare(jobs, start, epoch, days=90):
    until = start + timedelta(days=days)
    expected, changes = [], []
    for job in sorted(jobs, key=lambda j: j["name"]):
        name = job["name"]
        if name in SLOTS and job["cron"] != SLOTS[name][1]:
            raise ValueError("timer/cron slot mismatch: " + name)
        mi, hr, dom, month, dow = job["cron"].split()
        if dom != "*" or month != "*" or dow not in ("*", "1-5", "0", "1", "6"):
            raise ValueError("unsupported investment schedule: " + job["cron"])
        current = start.replace(hour=int(hr), minute=int(mi), second=0, microsecond=0)
        while current < until:
            weekday = (current.weekday() + 1) % 7
            if current >= start and (dow == "*" or dow == "1-5" and 1 <= weekday <= 5 or dow == str(weekday)):
                stamp = current.strftime("%Y%m%dT%H%M")
                executor = "home-ubuntu systemd timer" if name in SLOTS else "Gateway cron"
                expected.append(dict(execution_id=f"cron:{name}:{stamp}",
                    job={"daily-rec-verify": "daily-track", "thesis-enrich-daily": "thesis-enrich"}.get(name, name),
                    run_id=f"{name}-{stamp}", scope_epoch=epoch, market=None,
                    trade_date=current.date().isoformat(), scheduled_at=current.isoformat(),
                    deadline_at=(current + timedelta(hours=2)).isoformat(),
                    calendar=dict(mode="always", source=f"{executor} {name}: {job['cron']}; 不是开市断言",
                                  verified_at=start.isoformat())))
            current += timedelta(days=1)
        changes.append({"name": name, "before": job, "after": {
            **job, "prompt": compact(name, job["prompt"]), "enabled": name not in SLOTS}})
    expected.sort(key=lambda j: (j["scheduled_at"], j["job"]))
    plan = dict(generated_at=start.isoformat(), valid_from=start.isoformat(),
                valid_until=until.isoformat(), scope_epoch=epoch, expected=expected, executions=[],
                history_policy="切换前的记录和缺口保留在原归档；此清单不回溯填补。")
    return {"plan": plan, "changes": changes}


def units(delivery):
    if not re.fullmatch(r"feishu:oc_[a-zA-Z0-9]+", delivery):
        raise ValueError("not a main Feishu group: " + delivery)
    chat = delivery.split(":")[1]
    yield "investment-cron@.service", (
        "[Unit]\nDescription=Investment deterministic producer %i\n"
        f"[Service]\nType=exec\nWorkingDirectory={HERE}\n"
        f"ExecStart={CLI} run %i --chat {chat}\nRuntimeMaxSec=620\n"
        "Environment=TZ=Asia/Shanghai\n"
    )
    for job, (_, _, calendar) in SLOTS.items():
        yield f"investment-cron-{job}.timer", (
            f"[Unit]\nDescription=Investment schedule: {job}\n"
            f"[Timer]\nOnCalendar={calendar} Asia/Shanghai\n"
            "AccuracySec=1s\nPersistent=false\n"
            f"Unit=investment-cron@{job}.service\n"
            "[Install]\nWantedBy=timers.target\n"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    a = parser.parse_args()
    if a.start.tzinfo is None or a.start.utcoffset() != datetime.now(TZ).utcoffset():
        parser.error("--start requires Beijing time")
    jobs = json.loads(a.snapshot.read_text())
    if len(jobs) != 16 or set(SLOTS) - {j["name"] for j in jobs} or not all(
            j["enabled"] and not j["one_shot"] and j["domain"] == "investment" for j in jobs):
        parser.error("expected exactly 16 enabled investment jobs")
    delivery, = {j["delivery"] for j in jobs if j["name"] in SLOTS}
    a.out.mkdir(parents=True, exist_ok=False)
    changes = prepare(jobs, a.start.astimezone(TZ), json.loads(SCOPE.read_text())["scope_epoch"])
    (a.out / "cutover.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2) + "\n")
    for name, body in units(delivery):
        (a.out / name).write_text(body)


if __name__ == "__main__":
    main()

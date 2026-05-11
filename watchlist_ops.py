"""watchlist_ops.py — 关注清单的变更流 (提议 + 审批 + 应用).

架构:
    1. sunday-preview cron 生成 watchlist/proposed/<date>.yaml (不改 watchlist.yaml)
    2. 用户在飞书 chat 里用自然语言审批, 例如 "同意 1,2 拒绝 3"
    3. Agent (当前对话 Claude) 解析成 decisions dict {pid: bool}
       → 调 apply_proposal(proposed_path, decisions)
    4. 本模块: 改 watchlist.yaml (atomic) + append watchlist_changes.jsonl + git commit

审计语义:
    - changes.jsonl 是 append-only 的变更历史, 每行一个 op
    - git commit msg 包含 proposed filename + 变更清单
    - atomic write: yaml 用 .tmp + rename 避免半写

Decisions 约定:
    {1: True, 2: False, 4: True}  # 未出现的 pid 默认 False (拒绝)
    明确要求用户 "同意 X" 才 True, 静默场景一律不执行.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_YAML_PATH = _HERE / "watchlist.yaml"
_CHANGES_LOG = _HERE / "watchlist_changes.jsonl"
PROPOSED_DIR = _HERE / "watchlist" / "proposed"


# ─── proposed 加载 ─────────────────────────────────────────────────────────


def load_proposed(path: str | Path) -> dict[str, Any]:
    """Load & validate a proposed-changes YAML.

    Expected schema:
        schema_version: 1
        generated_at: ISO-8601 ts
        generated_by: free-form str (e.g. "weekend-sunday-preview cron")
        review_doc: URL to Feishu doc (optional)
        proposals:
          - id: int
            op: add | update | remove
            # add: entry: {code, name, tier, themes?, reason?, ...}
            # update: code: str, changes: {tier?, themes?, reason_append?, ...}
            # remove: code: str, reason: str
    """
    p = Path(path).expanduser().resolve()
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("schema_version") != 1:
        raise ValueError(f"proposed schema_version != 1: {p}")
    props = data.get("proposals")
    if not isinstance(props, list):
        raise ValueError(f"proposed.proposals must be a list: {p}")
    ids = [p_.get("id") for p_ in props]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate proposal ids in {p}: {ids}")
    return data


# ─── 内部工具 ──────────────────────────────────────────────────────────────


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML atomically (.tmp + rename), preserving unicode + key order."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=120)
    tmp.replace(path)


def _load_watchlist() -> dict[str, Any]:
    with _YAML_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _append_changes_log(records: list[dict[str, Any]]) -> None:
    _CHANGES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _CHANGES_LOG.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _git_commit(message: str, files: list[Path]) -> str:
    """git add + commit. Returns short sha, or empty string on no-op."""
    subprocess.run(
        ["git", "-C", str(_HERE), "add", "--"] + [str(f) for f in files],
        check=True, capture_output=True, text=True,
    )
    r = subprocess.run(
        ["git", "-C", str(_HERE), "commit", "-m", message],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        combined = (r.stdout or "") + (r.stderr or "")
        if "nothing to commit" in combined:
            return ""
        raise RuntimeError(f"git commit failed: {combined.strip()}")
    sha = subprocess.run(
        ["git", "-C", str(_HERE), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return sha


# ─── 主流程 ────────────────────────────────────────────────────────────────


def apply_proposal(
    proposed_path: str | Path,
    decisions: dict[int, bool],
    *,
    approved_by: str = "user",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a subset of proposed changes per the decisions dict.

    Parameters
    ----------
    proposed_path : path to a proposed YAML file (watchlist/proposed/YYYY-MM-DD.yaml)
    decisions : {proposal_id: approved}. Only True values are applied; missing
                or False values are skipped. Default-deny.
    approved_by : audit tag (logged in changes.jsonl).
    dry_run : compute diff only; do not touch disk / git.

    Returns
    -------
    dict:
      applied  : list of {id, op, code, name, ...}
      skipped  : list of {id, op, reason}
      commit   : "<sha>" on success | "" if nothing changed | None if dry_run
    """
    proposed = load_proposed(proposed_path)
    wl = _load_watchlist()
    entries: list[dict[str, Any]] = wl.setdefault("entries", [])
    by_code: dict[str, int] = {e["code"]: i for i, e in enumerate(entries)}

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    change_records: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    for p in proposed["proposals"]:
        pid = p["id"]
        op = p.get("op", "?")
        if not decisions.get(pid, False):
            skipped.append({"id": pid, "op": op, "reason": "not approved"})
            continue

        try:
            if op == "add":
                entry = dict(p["entry"])  # copy to avoid mutating proposed
                entry.setdefault("added_at", now_iso[:10])
                code = entry["code"]
                if code in by_code:
                    skipped.append({"id": pid, "op": op, "reason": f"code {code} already in watchlist"})
                    continue
                entries.append(entry)
                by_code[code] = len(entries) - 1
                applied.append({"id": pid, "op": "add", "code": code, "name": entry.get("name")})
                change_records.append({
                    "ts": now_iso, "op": "add", "code": code, "name": entry.get("name"),
                    "tier": entry.get("tier"), "themes": entry.get("themes"),
                    "reason": entry.get("reason"),
                    "source": Path(proposed_path).name, "proposal_id": pid,
                    "approved_by": approved_by,
                })

            elif op == "remove":
                code = p["code"]
                if code not in by_code:
                    skipped.append({"id": pid, "op": op, "reason": f"code {code} not in watchlist"})
                    continue
                idx = by_code[code]
                removed = entries.pop(idx)
                # rebuild index (idx after pop is stale)
                by_code = {e["code"]: i for i, e in enumerate(entries)}
                applied.append({"id": pid, "op": "remove", "code": code, "name": removed.get("name")})
                change_records.append({
                    "ts": now_iso, "op": "remove", "code": code, "name": removed.get("name"),
                    "reason": p.get("reason"),
                    "source": Path(proposed_path).name, "proposal_id": pid,
                    "approved_by": approved_by,
                })

            elif op == "update":
                code = p["code"]
                if code not in by_code:
                    skipped.append({"id": pid, "op": op, "reason": f"code {code} not in watchlist"})
                    continue
                entry = entries[by_code[code]]
                changes = dict(p.get("changes") or {})
                reason_append = changes.pop("reason_append", None)
                before = {k: entry.get(k) for k in changes}
                entry.update(changes)
                if reason_append:
                    prev = entry.get("reason", "") or ""
                    entry["reason"] = (prev + "; " + reason_append) if prev else reason_append
                entry["updated_at"] = now_iso[:10]
                changed_keys = list(changes.keys()) + (["reason (appended)"] if reason_append else [])
                applied.append({
                    "id": pid, "op": "update", "code": code, "name": entry.get("name"),
                    "changes": changed_keys,
                })
                change_records.append({
                    "ts": now_iso, "op": "update", "code": code, "name": entry.get("name"),
                    "before": before, "after": changes,
                    "reason_append": reason_append,
                    "source": Path(proposed_path).name, "proposal_id": pid,
                    "approved_by": approved_by,
                })

            else:
                skipped.append({"id": pid, "op": op, "reason": f"unknown op {op!r}"})

        except KeyError as e:
            skipped.append({"id": pid, "op": op, "reason": f"missing field {e}"})

    if dry_run:
        return {"applied": applied, "skipped": skipped, "commit": None, "dry_run": True}

    if not applied:
        return {"applied": [], "skipped": skipped, "commit": ""}

    # Atomically persist
    _atomic_write_yaml(_YAML_PATH, wl)
    _append_changes_log(change_records)

    pf = Path(proposed_path)
    head = f"watchlist: apply proposal {pf.name} (+{sum(1 for a in applied if a['op']=='add')} ~{sum(1 for a in applied if a['op']=='update')} -{sum(1 for a in applied if a['op']=='remove')})"
    body = [head, ""]
    for a in applied:
        body.append(f"  [{a['op']}] {a['code']} {a.get('name','') or ''}")
    commit_msg = "\n".join(body)
    sha = _git_commit(commit_msg, [_YAML_PATH, _CHANGES_LOG])

    return {"applied": applied, "skipped": skipped, "commit": sha}


# ─── CLI (手工触发 / 调试) ─────────────────────────────────────────────────


def _cli() -> None:
    import argparse, sys

    ap = argparse.ArgumentParser(description="Watchlist proposed-changes apply tool.")
    ap.add_argument("proposed_file", help="Path to watchlist/proposed/YYYY-MM-DD.yaml")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--approve", type=str, default="",
                     help="Comma-separated approved proposal ids, e.g. '1,2,4'")
    grp.add_argument("--all", action="store_true", help="Approve all proposals")
    grp.add_argument("--show", action="store_true", help="Just show the proposed content, do nothing")
    ap.add_argument("--dry-run", action="store_true", help="Compute diff but don't write / commit")
    ap.add_argument("--by", type=str, default="user", help="audit tag (default: user)")
    args = ap.parse_args()

    if args.show:
        prop = load_proposed(args.proposed_file)
        print(yaml.safe_dump(prop, allow_unicode=True, sort_keys=False, width=120))
        return

    prop = load_proposed(args.proposed_file)
    pids = [p["id"] for p in prop["proposals"]]

    if args.all:
        decisions = {pid: True for pid in pids}
    else:
        approved = {int(x) for x in args.approve.split(",") if x.strip()}
        decisions = {pid: (pid in approved) for pid in pids}

    result = apply_proposal(
        args.proposed_file, decisions,
        approved_by=args.by, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("applied") and not args.dry_run:
        sys.exit(4)  # nothing applied, non-zero exit


if __name__ == "__main__":
    _cli()

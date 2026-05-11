"""watchlist_data.py — 关注清单 (持仓 + 观察) 兼容 shim.

本模块内部读 `watchlist.yaml` 后填充同名全局变量与函数, 保留 v0 的 5 个
public symbol:
    WATCHLIST  — dict[tier, list[(code, name)]]
    US_ANCHORS — list[(code, name)]
    all_codes  — () -> list[(code, name, tier)]
    groups     — () -> WATCHLIST
    has_hk     — () -> bool

daily.sh / backtest.sh 继续 `from watchlist_data import ...` 无需修改.

维护规则:
    - 改 watchlist.yaml + git commit
    - 不要直接改本文件 (它只是 loader)
    - Agent 提议走 watchlist/proposed/*.yaml + watchlist_ops.apply_proposal

字段比 v0 丰富 (themes/added_at/reason 等), 取用新字段请走 entries():
    from watchlist_data import entries
    for e in entries(): print(e["code"], e.get("themes"), e.get("added_at"))
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_YAML_PATH = _HERE / "watchlist.yaml"


def _load() -> dict[str, Any]:
    with _YAML_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("schema_version") != 1:
        raise ValueError(
            f"watchlist.yaml schema_version != 1 (got {data.get('schema_version')!r}); "
            f"update watchlist_data.py loader or downgrade yaml"
        )
    return data


_DATA = _load()
_ENTRIES: list[dict[str, Any]] = list(_DATA.get("entries") or [])
_US_ANCHOR_ENTRIES: list[dict[str, Any]] = list(_DATA.get("us_anchors") or [])


def _build_watchlist_dict() -> dict[str, list[tuple[str, str]]]:
    """group entries by tier preserving file order within each tier."""
    out: dict[str, list[tuple[str, str]]] = {}
    for e in _ENTRIES:
        tier = e.get("tier") or "未分组"
        code = e["code"]
        name = e["name"]
        out.setdefault(tier, []).append((code, name))
    return out


# ─── v0-compatible public symbols ──────────────────────────────────────────

#: v0 format: dict[tier, list[(code, name)]]
WATCHLIST: dict[str, list[tuple[str, str]]] = _build_watchlist_dict()

#: v0 format: list[(code, name)] for US anchors
US_ANCHORS: list[tuple[str, str]] = [(a["code"], a["name"]) for a in _US_ANCHOR_ENTRIES]


def all_codes() -> list[tuple[str, str, str]]:
    """所有 ts_code (去重). Returns: [(code, name, tier), ...]."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for tier, stocks in WATCHLIST.items():
        for code, name in stocks:
            if code not in seen:
                seen.add(code)
                out.append((code, name, tier))
    return out


def has_hk() -> bool:
    """Check if any HK ticker in the watchlist."""
    for _tier, stocks in WATCHLIST.items():
        for code, _name in stocks:
            if code.endswith(".HK"):
                return True
    return False


def groups() -> dict[str, list[tuple[str, str]]]:
    """返回 {tier: [(code, name), ...]}."""
    return WATCHLIST


# ─── v1 new: richer-metadata access ────────────────────────────────────────


def entries() -> list[dict[str, Any]]:
    """Full entries list with all metadata (themes / added_at / reason / ...).

    Return a shallow copy of each entry dict so callers can't mutate the
    cached state. For mutation, use watchlist_ops.apply_proposal().
    """
    return [dict(e) for e in _ENTRIES]


def yaml_path() -> str:
    """Path to the backing watchlist.yaml (for tooling / ops)."""
    return str(_YAML_PATH)


# ─── CLI self-test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    codes = all_codes()
    print(f"关注清单 ({len(codes)} 只, 分 {len(WATCHLIST)} 组)  HK 标的: {'有' if has_hk() else '无'}")
    print(f"源文件: {_YAML_PATH}\n")
    for tier, stocks in WATCHLIST.items():
        print(f"  [{tier}]  {len(stocks)} 只")
        for code, name in stocks:
            tag = " 🇭🇰" if code.endswith(".HK") else ""
            # lookup richer metadata
            meta = next((e for e in _ENTRIES if e["code"] == code), {})
            themes = ",".join(meta.get("themes") or [])
            added = meta.get("added_at", "?")
            extra = f"  [since {added}]" + (f"  {{{themes}}}" if themes else "")
            print(f"    {code:<12} {name}{tag}{extra}")
        print()
    print(f"美股锚点 ({len(US_ANCHORS)} 只):")
    for code, name in US_ANCHORS:
        print(f"    {code:<6} {name}")

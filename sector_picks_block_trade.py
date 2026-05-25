"""sector_picks_block_trade.py — concept-level z_block 短线信号 (sector_picks 接入层).

🆕 P0-2 (2026-05-25): 把 z_block 大宗交易折溢价信号从 news_score 拒收后, 接入 sector_picks
   的短线层. 与 news_score 解耦.

回测背景 (详见 docs/news_score_phase2_design.md):
  - 11 BK-mapped concepts × 6mo panel (n=681 with valid z)
  - **fwd_5d IC = +0.123** ✅ (短线有效)
  - fwd_20d IC = +0.027 (中线无效, 故 news_score 拒收)
  - 增量 IC vs fund (fwd_5d) = +7.3% ✅
  - window_days=20 (5d 太稀疏), horizon=5d (短线层目标)

用途:
  本模块挂在 sector_picks 主流程的输出顶层 (`result["block_signal"]`),
  作为 **concept-level 短线诊断**, 给报告加一行 5d 风向参考. 当前 v1 **不影响个股
  verdict** (BUY/WATCH/AVOID), 只是 informational. v2 视实战观察决定是否进 evaluate().

接口:
  concept_block_signal(concept, as_of=None) -> BlockSignal | None

  z_block > +1.0: 折价较常态轻 / 出现溢价 → 5d 偏多
  z_block < -1.0: 折价异常深 → 5d 偏空
  |z| 不显著 (|z| < 0.5) → 短线无信号

数据源 + cache 完全复用 news_score_phase2_block 的 fetch/index/z_block_60d 函数,
避免 DRY 违反.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

# 复用 phase2_block 的数据层 (避免重复 cache + fetch 逻辑)
import news_score_phase2_block as _ph2b


_VERSION = "spbt_v1_2026-05-25"
# 与回测一致, 不要瞎改: window_days=20 经 IC=+0.123 验证. horizon=5d 是消费侧 (目标).
_DEFAULT_WINDOW_DAYS = 20


@dataclass
class BlockSignal:
    """concept-level 大宗交易折溢价短线信号 (5d horizon)."""
    concept: str
    as_of: str
    z_block_60d: float | None      # None = 数据不足 / 无信号
    pts: float                     # clip(z, -2, +2) * 1.0, 范围 [-2, +2]
    cur_premium_pct: float | None  # 当前 window 加权平均折溢价 %
    hist_mean_pct: float | None    # 历史 60d 均值
    hist_std_pct: float | None     # 历史 60d std
    n_trades: int                  # 当前 window 笔数
    n_stocks: int                  # 当前 window 涉及股数
    total_amt_wan: float           # 当前 window 总成交 (万元)
    n_hist_nonzero: int            # 历史有效样本数 (用于稳健性判断)
    window_days: int
    horizon: str                   # "fwd_5d" 固定; 提醒消费侧不要用 fwd_20d
    note: str                      # 一句话描述
    version: str = _VERSION


def concept_block_signal(
    concept: str,
    as_of: str | None = None,
    preloaded_by_ts: dict | None = None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    verbose: bool = False,
) -> BlockSignal | None:
    """对一个 concept 算 concept-level z_block 短线信号.

    Returns:
        BlockSignal — 数据不足时 z_block_60d=None 但仍返回 (用于报告渲染).
        None — concept 不在 CONCEPTS / 无成分股.

    preloaded_by_ts:
        如果上层已经预拉了 block_trade index (e.g. backtest 批量场景), 传进来
        避免重复 fetch. 否则本函数会 lazy fetch + cache.
    """
    from concepts_data import stocks_of

    as_of = as_of or datetime.now().strftime("%Y%m%d")
    members = stocks_of(concept) or []
    if not members:
        return None

    # 走 phase2_block 的核心入口 (复用 fetch + index)
    pts, note, diag = _ph2b.news_score_phase2_block(
        concept,
        as_of=as_of,
        preloaded_by_ts=preloaded_by_ts,
        window_days=window_days,
    )

    z = diag.get("z_block")
    cur = diag.get("cur")
    hm = diag.get("hist_mean")
    hs = diag.get("hist_std")
    n_trades = diag.get("n_trades", 0)
    n_stocks = diag.get("n_stocks", 0)
    amt = diag.get("total_amt_wan", 0)
    n_hist = diag.get("n_hist_nonzero", 0)

    # 重写 note 为短线层语义 (phase2_block 的 note 默认是 news_score 视角)
    if z is None:
        sig_note = f"📊 短线信号不足: {diag.get('reason', 'n/a')} (window {window_days}d, n_trades={n_trades})"
    else:
        if z >= 1.0:
            tone = "📈 偏多 (折价异常轻 / 溢价)"
        elif z <= -1.0:
            tone = "📉 偏空 (折价异常深, 短线提防)"
        else:
            tone = "中性"
        sig_note = (
            f"{tone}: z={z:+.2f}σ  cur={cur:+.2f}%  hist={hm:+.2f}±{hs:.2f}  "
            f"({n_trades} 笔 / {n_stocks} 股 / {amt:,.0f}万元, 5d horizon)"
        )

    return BlockSignal(
        concept=concept,
        as_of=as_of,
        z_block_60d=round(z, 3) if z is not None else None,
        pts=round(pts, 3),
        cur_premium_pct=round(cur, 3) if cur is not None else None,
        hist_mean_pct=round(hm, 3) if hm is not None else None,
        hist_std_pct=round(hs, 3) if hs is not None else None,
        n_trades=n_trades,
        n_stocks=n_stocks,
        total_amt_wan=amt,
        n_hist_nonzero=n_hist,
        window_days=window_days,
        horizon="fwd_5d",
        note=sig_note,
    )


def to_dict(sig: BlockSignal | None) -> dict[str, Any] | None:
    """Convenience: dataclass → dict for JSON output. None passthrough."""
    if sig is None:
        return None
    return asdict(sig)


# ─── CLI smoke test ──────────────────────────────────────────────────────────

def main():
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Concept-level z_block 短线信号 (sector_picks 接入).")
    ap.add_argument("--concept", required=True, help="concept name (concepts_data.CONCEPTS key)")
    ap.add_argument("--as-of", default=None, help="YYYYMMDD; default today")
    ap.add_argument("--window-days", type=int, default=_DEFAULT_WINDOW_DAYS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sig = concept_block_signal(args.concept, as_of=args.as_of,
                               window_days=args.window_days, verbose=True)
    if sig is None:
        print(f"❌ concept '{args.concept}' 无成分股 / 不在 CONCEPTS")
        return

    if args.json:
        print(json.dumps(to_dict(sig), ensure_ascii=False, indent=2))
    else:
        print(f"\n━━━ Block Trade Signal: {sig.concept} @ {sig.as_of} ━━━")
        print(f"  {sig.note}")
        print(f"  pts={sig.pts:+.2f}  (swing ±2.0, horizon=fwd_5d)")
        print(f"  hist 样本数: {sig.n_hist_nonzero}/60  (≥10 才算有效 z)")
        print(f"  version: {sig.version}")


if __name__ == "__main__":
    main()

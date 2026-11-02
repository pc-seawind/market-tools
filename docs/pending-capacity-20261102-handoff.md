# pending 累计记录上限修复与 investment 补录交接

工作项：`work_f8504a653ffde8cef3dd`。本文件是 code 交付，不是 investment 验收结论。

## 修改边界

`mt1/research_pool.py` 仅从 `LIMITS` 删除 `pending: 100`，不改观察 50、重点推荐 10。
累计 pending 存储不受单轮预算阻断；新研究与独立复核批次仍各默认最多 10 项。
source/epoch/scope hash/CAS、签审、append-only、幂等及已有 episode 行为不变。
未改用户 8 持仓、交易、外部自选、cron 或服务；未写生产研究账本、未重跑扫描/计算。
没有增加配置、fallback、兼容层或其他防御分支。

测试使用 home-ubuntu 本机串行临时合成账本；秒级状态依赖测试不分发多机，未启动平行 assistant。
使用现有 `/home/emox/work/homespace/.venv/bin/python`（pytest 9.0.3）；系统 python3 无 pytest，未安装依赖。
新增测试修改前复现 3 failed / 2 passed，修复后相关回归 54 passed / 25 subtests passed（6.19s）。
具体命令及结果见 `reports/work-f8504a65-20261102/tests-{before,after}.txt`；执行命令为：

```sh
timeout 90s /home/emox/work/homespace/.venv/bin/python -m pytest -q \
  tests/test_research_pool_capacity.py tests/test_research_pool_lifecycle.py \
  tests/test_research_pool_consumers.py tests/test_research_pool_revision2.py \
  tests/test_research_performance.py tests/test_research_plan_handoff.py
```

另以同一解释器运行 `tests/test_tracking_scope.py`，20 passed（0.18s），
见 `reports/work-f8504a65-20261102/tests-scope.txt`；合计 74 passed / 25 subtests passed。

本次独立提交文件清单（不包含其他已有未提交文件）：

- `mt1/research_pool.py`
- `tests/test_research_pool_capacity.py`
- `docs/research-pool-lifecycle.md`
- `docs/pending-capacity-20261102-handoff.md`
- `reports/work-f8504a65-20261102/blocked-input-audit.json`
- `reports/work-f8504a65-20261102/tests-before.txt`
- `reports/work-f8504a65-20261102/tests-after.txt`
- `reports/work-f8504a65-20261102/tests-scope.txt`
- `reports/work-f8504a65-20261102/protection-check.json`

## 11/2 原始拒写材料只读核验

原目录：`/home/emox/work/investment/reference/weekend-cycles/2026-11-02/20261102T183106-evening/current-evening-research`。
原 `event-receipts.json` 共 20 项：12 成功、8 拒写；12 个成功 request_id 均在账本历史。
8 项原 `.input.json`、`.stdout.json`、`.stderr.txt` SHA256 均匹配原回执；错误均为
`ValueError: pool capacity reached`。原文 source hash、来源时间顺序也匹配。

|代码|名称|原输入相对路径|
|---|---|---|
|000417.SZ|合百集团|events/000417.SZ.input.json|
|000554.SZ|泰山石油|events/000554.SZ.input.json|
|000523.SZ|红棉股份|events/000523.SZ.input.json|
|000598.SZ|兴蓉环境|events/000598.SZ.input.json|
|000407.SZ|胜利股份|events/000407.SZ.input.json|
|000100.SZ|TCL科技|events/000100.SZ.input.json|
|000551.SZ|创元科技|events/000551.SZ.input.json|
|000617.SZ|中油资本|events/000617.SZ.input.json|

只读核验时，8 项均 expected_version=0、当前版本=0、request_id 尚未入账、state=pending、
research_outcome=partially_researched；研究时间保留原 2026-11-02 19:07:11—12 +08:00。
当前账本 watch=2 / pending=100 / recommended=0，scope 含 8 持仓，epoch 为
`user-reset-20260911T120928Z`，原 scope SHA256 为
`db353f7aac35a0f79aec1ecc2898e46eeb23025ffd7e713565270be784444a18`。
当时无 scope/epoch/CAS 冲突，**不是承诺稍后状态仍相同**。
逐文件绝对路径/hash、研究时间、source、回执、核验时刻见
`reports/work-f8504a65-20261102/blocked-input-audit.json`。

## 仅由原 investment topic 执行的补录与读回

先独立验收代码；再次核对 scope/epoch/CAS 和原材料 hash。以下只提交已有 8 份原输入，
不重跑 `record-research.py`（它会重新生成时间并覆盖旧回执），不改写原研究时间/来源/判断。
新 CLI 进程直接加载本机工作树，无需重启服务。代码不代 investment 执行：

```bash
set -euo pipefail
cd /home/emox/work/projects/market-tools
E=/home/emox/work/investment/reference/weekend-cycles/2026-11-02/20261102T183106-evening/current-evening-research
S=/home/emox/work/investment/reference/tracking-scope.json
OUT=$(mktemp -d "$E/pending-capacity-replay.XXXXXXXX")
printf '本次新回执目录：%s\n' "$OUT"
for code in 000417.SZ 000554.SZ 000523.SZ 000598.SZ 000407.SZ 000100.SZ 000551.SZ 000617.SZ; do
  timeout 30s python3 mt1.py --scope "$S" research-event --input "$E/events/$code.input.json" \
    > "$OUT/$code.stdout.json" 2> "$OUT/$code.stderr.txt"
done
timeout 30s python3 mt1.py --scope "$S" research-status --out "$OUT/research-pool.json"
python3 - "$E" "$OUT" <<'PY'
import json, sys
from pathlib import Path
e, out = map(Path, sys.argv[1:])
snapshot = json.loads((out/'research-pool.json').read_text())
events = {r['request_id']: r for r in snapshot['events']}
for code in '000417.SZ 000554.SZ 000523.SZ 000598.SZ 000407.SZ 000100.SZ 000551.SZ 000617.SZ'.split():
    original = json.loads((e/'events'/(code+'.input.json')).read_text())
    receipt = json.loads((out/(code+'.stdout.json')).read_text())
    recorded = events[original['request_id']]
    assert recorded == receipt
    assert all(recorded[k] == v for k, v in original.items())
    assert recorded['version'] == original['expected_version'] + 1
    assert recorded['state'] == 'pending'
print('8 项原输入追加及事件读回一致；当前计数：', snapshot['counts'])
PY
```

若无并发事件，补录后预期 watch=2 / pending=108 / recommended=0；这尚未实际执行。
scope hash/epoch 或版本冲突必须报告并停止，不自动更新输入的 hash/version 或研究时间；
已超过 7 天的原研究也不伪造新时间绕过，应由 investment 真正重新研究。
部分成功后可以原输入稳定 request_id 重试；已经成功的事件幂等返回，不重复追加。
上面脚本每次创建新目录，失败 stderr 保存在打印的目录；旧晚报、补记和原回执均不覆盖。
原 topic 仍需独立核验真实研究质量、实际补录与用户可见读回，code 不自行标验收通过。

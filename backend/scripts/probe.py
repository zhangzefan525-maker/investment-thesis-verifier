"""P1 探针硬门：拿到 Key 后的第一件事。

三个**最小有界**探针，验证计划里识别的 R1 风险（Key 拿到但 capability 没开）：
  1. 鉴权     —— Key 本身有效吗
  2. 行情     —— 历史 K 线能不能取（重建估值序列的前置）
  3. 财务+估值 —— 报表与估值能不能取（判定逻辑的前置）

用单标的、小窗口，不触发任何大结果规则。全绿才继续；不绿就立刻切 fixture 策略，
不写任何依赖真接口的代码。这正是计划里 R1 的对冲动作。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import Err, Ok  # noqa: E402
from app.providers.fuyao import FuyaoClient, credential_status  # noqa: E402

PROBE_TARGET = "600519.SH"
GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def line(ok: bool, title: str, detail: str) -> None:
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {title}")
    if detail:
        for l in detail.splitlines():
            print(f"         {DIM}{l}{RESET}")


def main() -> int:
    status = credential_status()
    print(f"\n凭据来源: {status['origin']}")
    if not status["present"]:
        print(f"{RED}未找到凭据，探针无法执行。{RESET}")
        print("获取方式: https://fuyao.aicubes.cn/admin/ （注意保留末尾斜杠，")
        print("          不带斜杠会从 HTTPS 降级到 HTTP 导致扫码登录态丢失）")
        return 1

    c = FuyaoClient()
    now = datetime.now(timezone.utc)
    results: list[bool] = []

    # --- 探针 1：鉴权 -----------------------------------------------------
    print("\n探针 1/3 · 鉴权")
    r = c.search_ticker(PROBE_TARGET)
    if isinstance(r, Ok):
        line(True, "标的检索成功", f"命中 {len(r.value)} 条，request_id={r.provenance.request_id}")
        results.append(True)
    else:
        line(False, "标的检索失败", f"{r.detail.category.value}\n{r.detail.failure_evidence}")
        results.append(False)
        print(f"\n{RED}鉴权未通过。后续探针无意义，提前终止。{RESET}")
        print("请检查：Key 是否完整复制、是否已在管理页签发、X-api-key 是否被正确传递。")
        return 2

    # --- 探针 2：行情（小窗口，30 天） ------------------------------------
    print("\n探针 2/3 · 行情（历史 K 线，小窗口）")
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    r2 = c.price_historical(PROBE_TARGET, start_ms, end_ms, "forward")
    if isinstance(r2, Ok):
        bar = (r2.value or [{}])[-1]
        line(True, f"历史 K 线成功（{len(r2.value)} 根）",
             f"最新 {bar.get('date_ms')} 收盘 {bar.get('close_price')}，口径前复权")
        results.append(True)
    else:
        line(False, "历史 K 线失败", f"{r2.detail.category.value}\n{r2.detail.failure_evidence}")
        results.append(False)

    # --- 探针 3：财务 + 估值 ----------------------------------------------
    print("\n探针 3/3 · 财务与估值")
    r3 = c.income_statements(PROBE_TARGET, "quarterly", 4)
    if isinstance(r3, Ok):
        lat = (r3.value or [{}])[0]
        line(True, f"利润表成功（{len(r3.value)} 期）",
             f"最新 {lat.get('fiscal_year')}{lat.get('fiscal_period')} "
             f"营收 {lat.get('operating_income')} 归母净利 {lat.get('parent_holder_net_profit')}")
        results.append(True)
    else:
        line(False, "利润表失败", f"{r3.detail.category.value}\n{r3.detail.failure_evidence}")
        results.append(False)

    r4 = c.valuation_snapshot([PROBE_TARGET])
    if isinstance(r4, Ok):
        item = (r4.value or [{}])[0]
        line(True, "估值快照成功",
             f"pe_ttm={item.get('pe_ttm')} pb_mrq={item.get('pb_mrq')} ps_ttm={item.get('ps_ttm')}")
        results.append(True)
    else:
        line(False, "估值快照失败", f"{r4.detail.category.value}\n{r4.detail.failure_evidence}")
        results.append(False)

    passed = sum(results)
    print(f"\n{'=' * 60}")
    if all(results):
        print(f"{GREEN}探针全绿（{passed}/{len(results)}）。可以继续走实时数据路线。{RESET}")
        print("\n下一步：")
        print(f"  python scripts/capture.py {PROBE_TARGET} <第二个标的>")
        return 0

    print(f"{RED}探针未全绿（{passed}/{len(results)}）。按计划 R1，切 fixture 策略。{RESET}")
    print("不要在未通过的路径上继续写依赖真接口的代码——那会在演示当天炸。")
    print("未通过的探针，其对应子问题在产品里会如实返回「无法验证」并说明原因，")
    print("功能上依然完整可交付。")
    return 3


if __name__ == "__main__":
    sys.exit(main())

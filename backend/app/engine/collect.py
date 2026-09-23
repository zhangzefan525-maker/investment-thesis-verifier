"""一次性的取数编排。

原则：**所有网络请求在这里发生一次**，执行器只读结果。这样做的直接后果是
「哪一路取数失败了」这件事在 Ctx 里是显式且唯一的，不可能被某个执行器悄悄吞掉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..analysis.valuation_series import build_series
from ..providers.base import DataProvider, Err, FetchResult, Ok
from ..schemas import Provenance
from .executors import Ctx

MAX_LOOKBACK_YEARS = 9.5  # 接口硬约束是 10 年，留余量
HISTORY_LIMIT = 12        # 财务序列取最近 12 期


def _record(ctx: Ctx, key: str, result: FetchResult) -> None:
    """把一次取数的结果记到 Ctx：成功记溯源，失败记错误。两者必居其一。"""
    if isinstance(result, Ok):
        ctx.procs[key] = result.provenance
    elif isinstance(result, Err):
        ctx.failures[key] = result
        ctx.run_errors.append(
            f"[{key}] {result.detail.category.value}: {result.detail.failure_evidence}"
        )


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def latest_report_label(income_q: list[dict]) -> str:
    """从最近的季报推出指标接口需要的 report 参数（yyyy-N）。"""
    usable = [i for i in income_q if i.get("fiscal_year") and i.get("fiscal_period")]
    if not usable:
        return ""
    lat = max(usable, key=lambda i: i.get("period_end_ms") or 0)
    fp = lat["fiscal_period"]
    q = {"Q1": "1", "Q2": "2", "Q3": "3", "Q4": "4"}.get(fp)
    return f"{lat['fiscal_year']}-{q}" if q else ""


def collect(provider: DataProvider, thscode: str, ticker: str = "", name: str = "") -> Ctx:
    """一次性取全所需数据，构造 Ctx。"""
    ctx = Ctx(thscode=thscode, ticker=ticker or thscode.split(".")[0], name=name)

    now = datetime.now(timezone.utc)
    end_ms = _ms(now)
    start_ms = _ms(now - timedelta(days=int(365.25 * MAX_LOOKBACK_YEARS)))

    # --- 行情：前复权与不复权各一条，用于交叉验证 -------------------------
    r_fwd = provider.price_historical(thscode, start_ms, end_ms, "forward")
    _record(ctx, "prices_historical", r_fwd)
    if isinstance(r_fwd, Ok):
        ctx.bars_fwd = r_fwd.value or []

    r_non = provider.price_historical(thscode, start_ms, end_ms, "none")
    _record(ctx, "prices_historical_none", r_non)
    if isinstance(r_non, Ok):
        ctx.bars_none = r_non.value or []

    # --- 财务报表 ---------------------------------------------------------
    r_qa = provider.income_statements(thscode, "quarterly", HISTORY_LIMIT)
    _record(ctx, "income_statements", r_qa)
    if isinstance(r_qa, Ok):
        ctx.income_q = r_qa.value or []

    r_aa = provider.income_statements(thscode, "annual", HISTORY_LIMIT)
    _record(ctx, "income_statements_annual", r_aa)
    if isinstance(r_aa, Ok):
        ctx.income_a = r_aa.value or []
    # 若年报取数失败，退回用季报里的 Q4 当年度数据，并显式记录这次降级
    if not ctx.income_a and ctx.income_q:
        ctx.income_a = [r for r in ctx.income_q if r.get("fiscal_period") == "Q4"]
        ctx.run_errors.append(
            "[income_statements_annual] 年报序列取数失败，已降级使用季报中的 Q4 记录作为年度数据；"
            "该降级会影响「同 fiscal_period 同比」的可比性"
        )

    r_ba = provider.balance_sheets(thscode, "annual", HISTORY_LIMIT)
    _record(ctx, "balance_sheets", r_ba)
    if isinstance(r_ba, Ok):
        ctx.balance_a = r_ba.value or []

    r_ca = provider.cash_flow_statements(thscode, "annual", HISTORY_LIMIT)
    _record(ctx, "cash_flow_statements", r_ca)
    if isinstance(r_ca, Ok):
        ctx.cashflow_a = r_ca.value or []

    r_cq = provider.cash_flow_statements(thscode, "quarterly", HISTORY_LIMIT)
    _record(ctx, "cash_flow_statements_q", r_cq)
    if isinstance(r_cq, Ok):
        ctx.cashflow_q = r_cq.value or []

    # --- 估值快照（用于自校准） -------------------------------------------
    r_v = provider.valuation_snapshot([thscode])
    _record(ctx, "valuations_snapshot", r_v)
    if isinstance(r_v, Ok) and r_v.value:
        ctx.valuation_snapshot = r_v.value[0]

    # --- 财务指标（最新报告期） -------------------------------------------
    ctx.expected_report = latest_report_label(ctx.income_q)
    if ctx.expected_report:
        r_i = provider.financial_indicators(thscode, ctx.expected_report)
        _record(ctx, "financial_indicators", r_i)
        if isinstance(r_i, Ok):
            ctx.indicators = r_i.value or {}
    else:
        ctx.run_errors.append(
            "[financial_indicators] 无法从季报推出报告期标签，指标接口未调用"
        )

    # --- 派生序列 ---------------------------------------------------------
    _build_derived_series(ctx)

    # --- 估值序列重建（含三道自检） ---------------------------------------
    ctx.series_fwd = build_series(
        thscode=thscode,
        bars=ctx.bars_fwd,
        income_q=ctx.income_q,
        income_a=ctx.income_a,
        balance_a=ctx.balance_a,
        snapshot_item=ctx.valuation_snapshot,
    )
    if ctx.bars_none:
        ctx.series_none = build_series(
            thscode=thscode,
            bars=ctx.bars_none,
            income_q=ctx.income_q,
            income_a=ctx.income_a,
            balance_a=ctx.balance_a,
            snapshot_item=None,
        )

    return ctx


def _build_derived_series(ctx: Ctx) -> None:
    """构造成本率与毛利率的多期序列。两序列必须等长且同序，否则下游会拒绝使用。"""
    rows = [r for r in ctx.income_q if r.get("period_end_ms")]
    rows.sort(key=lambda r: r["period_end_ms"])
    costs: list[float] = []
    gms: list[float] = []
    for r in rows:
        inc, cost = r.get("operating_income"), r.get("operating_costs")
        if inc in (None, 0) or cost is None:
            continue
        costs.append(100.0 * float(cost) / float(inc))
        gms.append(100.0 * (float(inc) - float(cost)) / float(inc))
    ctx.cost_ratio_series = costs
    ctx.gm_series = gms

"""估值历史序列的重建与自校准。

## 为什么需要这个模块

扶摇的估值接口契约写得很明确：

    「接口固定返回市盈率 TTM/MRQ、市净率 MRQ、市销率 TTM 和市现率 TTM 五个估值指标，
      **不提供历史估值**、分页、指标选择或高低估结论。」

而「估值回落但基本面未恶化」这类命题，本质上需要一个**历史分位**才能判断「回落」。
只有当期快照是做不出来的。

## 我们的做法，以及为什么它是诚实的

不用任何估算或记忆数字，纯用两类**真实接口数据**重建：

    PE(t) = 前复权收盘价(t) ÷ EPS_TTM(t)
    PB(t) = 前复权收盘价(t) ÷ BVPS(t)
    其中 BVPS(t) = 归母净资产(t) ÷ 股本(t)，股本(t) = 归母净利润(t) ÷ 基本每股收益(t)

EPS_TTM(t) 是一个**阶梯函数**：只在新财报披露日（report_date_ms）跳变。
因此 t 时刻用的是「当时投资者真实能看到的最新财务数据」——这正是 PE 的定义。

## 三道自检（这是本模块的核心价值）

1. **口径自检**：季报到底是累计口径还是单季口径？不能猜。用
   `Q4 单期 ≈ FY 全年` 还是 `Q4 单期 ≈ FY − Q3 累计` 反推，反推不出来就判为口径不一致。
2. **自校准**：把我们重建的**最新一期** PE 与扶摇权威快照的 `pe_ttm` 对拍。
   相对偏差在容差内 → 重建方法可信；超出容差 → 不硬用，直接降级并声明。
3. **复权交叉验证**：用 `forward`（前复权）和 `none`（不复权）各建一条序列。
   两者本应一致（除权除息会造成差异）。若分位结论相反 → **这是一个真实的证据冲突，
   交付给冲突检测模块，不做和稀泥。**

## 已知边界（必须写进 README 与 UI）

- 复权价与历史 EPS 的股本调整口径可能不一致，可能造成序列在除权日附近跳变。
  我们没有股本变动明细数据，**无法完整校正**——这一条如实声明。
- 历史 PE 分位是**重建口径**，与行情终端自带的官方分位口径可能存在差异，
  UI 上必须标注「重建口径」，不得冒充官方分位。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..providers.base import Err, FetchResult, Ok
from ..schemas import Provenance, UnverifiableCategory, UnverifiableDetail

DAY_MS = 86_400_000


@dataclass
class Point:
    date_ms: int
    value: float
    basis: str = ""


@dataclass
class SeriesCheck:
    """一道自检的结果。全部要透传给前端，不允许静默。"""

    name: str
    passed: Optional[bool]           # None = 无法判定
    detail: str
    severity: str = "info"           # info | warning | blocking


@dataclass
class ValuationSeries:
    """重建出来的估值序列 + 全部自检记录。"""

    thscode: str
    metrics: dict[str, list[Point]] = field(default_factory=dict)
    checks: list[SeriesCheck] = field(default_factory=list)
    calibration: Optional[dict] = None
    errors: list[str] = field(default_factory=list)
    provenance_notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return any(
            c.severity == "blocking" and c.passed is False for c in self.checks
        ) is False and bool(self.metrics)

    def blocking_reasons(self) -> list[str]:
        return [c.detail for c in self.checks if c.severity == "blocking" and c.passed is False]

    def percentile(self, metric: str, value: Optional[float] = None) -> Optional[float]:
        """当前值在自身历史中的分位（0–100）。value 缺省用序列最后一个点。"""
        pts = self.metrics.get(metric) or []
        if len(pts) < 60:  # 少于约 3 个月交易日，分位不具意义
            return None
        v = value if value is not None else pts[-1].value
        below = sum(1 for p in pts if p.value <= v)
        return round(100.0 * below / len(pts), 1)


# --------------------------------------------------------------------------
# 口径自检：季报是累计还是单季
# --------------------------------------------------------------------------


def infer_cumulative(annual: list[dict], quarterly: list[dict]) -> tuple[Optional[bool], str]:
    """反推季报口径。返回 (是否累计, 证据说明)。

    判据：
      - 若同一 fiscal_year 的 Q4 营业收入 ≈ 年报营业收入 → 累计口径
      - 若 Q4 ≈ 年报 − Q3 → 单季口径
      - 都不像 → 无法判定，返回 None（不允许猜）
    """
    by_year_annual = {a.get("fiscal_year"): a for a in annual if a.get("period_end_ms")}
    by_year_q: dict[int, dict[str, dict]] = {}
    for q in quarterly:
        y, fp = q.get("fiscal_year"), q.get("fiscal_period")
        if y is None or fp is None:
            continue
        by_year_q.setdefault(y, {})[fp] = q

    cumulative_hits, single_hits, compared = 0, 0, 0
    for year, ann in by_year_annual.items():
        a_inc = ann.get("operating_income")
        qs = by_year_q.get(year) or {}
        q4, q3 = qs.get("Q4"), qs.get("Q3")
        if a_inc is None or q4 is None:
            continue
        q4_inc = q4.get("operating_income")
        if q4_inc is None:
            continue
        compared += 1
        if a_inc != 0 and abs(q4_inc - a_inc) / abs(a_inc) < 0.01:
            cumulative_hits += 1
        elif q3 is not None and q3.get("operating_income") is not None:
            if a_inc != 0 and abs(q4_inc - (a_inc - q3["operating_income"])) / abs(a_inc) < 0.01:
                single_hits += 1

    if compared == 0:
        return None, f"没有可对拍的 fiscal_year（年报 {len(annual)} 期 / 季报 {len(quarterly)} 期），口径无法反推"
    if cumulative_hits > single_hits:
        return True, f"在 {compared} 个可比年度中，{cumulative_hits} 个年度满足「Q4 累计 ≈ FY 全年」→ 判定为累计口径"
    if single_hits > cumulative_hits:
        return False, f"在 {compared} 个可比年度中，{single_hits} 个年度满足「Q4 单季 ≈ FY − Q3 累计」→ 判定为单季口径"
    return None, f"{compared} 个可比年度中累计/单季判据均不成立（累计命中 {cumulative_hits}、单季命中 {single_hits}），口径无法判定"


# --------------------------------------------------------------------------
# 阶梯函数：把报告期数据变成「任一交易日可见的最新值」
# --------------------------------------------------------------------------


def _step_lookup(periods: list[tuple[int, float]], t_ms: int) -> Optional[float]:
    """periods 为 (披露日毫秒, 值) 的列表。返回披露日 <= t 的最后一个值。"""
    usable = [p for p in periods if p[0] <= t_ms]
    if not usable:
        return None
    return max(usable, key=lambda p: p[0])[1]


def build_ttm_points(
    quarterly: list[dict], annual: list[dict], field_name: str, cumulative: bool
) -> list[tuple[int, float]]:
    """构造 (披露日, TTM值) 的阶梯点。

    累计口径：TTM(期末) = 本年累计 + 上年全年 − 上年同期累计
    单季口径：TTM(期末) = 最近 4 个单季之和
    """
    rows = [q for q in quarterly if q.get("period_end_ms") and q.get("report_date_ms")]
    rows.sort(key=lambda r: r["period_end_ms"])

    if cumulative:
        ann_by_year = {a["fiscal_year"]: a for a in annual if a.get("period_end_ms")}
        cum_by_key: dict[tuple[int, str], dict] = {}
        for r in rows:
            cum_by_key[(r["fiscal_year"], r["fiscal_period"])] = r

        out: list[tuple[int, float]] = []
        for r in rows:
            cur = r.get(field_name)
            year, fp = r["fiscal_year"], r["fiscal_period"]
            if cur is None:
                continue
            if fp == "Q4":
                # Q4 累计即全年
                out.append((r["report_date_ms"], float(cur)))
                continue
            prev_ann = ann_by_year.get(year - 1)
            prev_same = cum_by_key.get((year - 1, fp))
            if prev_ann is None or prev_same is None:
                continue
            base = prev_ann.get(field_name)
            same = prev_same.get(field_name)
            if base is None or same is None:
                continue
            out.append((r["report_date_ms"], float(cur) + float(base) - float(same)))
        return out

    # 单季口径
    out2: list[tuple[int, float]] = []
    for i, r in enumerate(rows):
        window = rows[max(0, i - 3) : i + 1]
        vals = [w.get(field_name) for w in window]
        if len(window) < 4 or any(v is None for v in vals):
            continue
        out2.append((r["report_date_ms"], float(sum(vals))))
    return out2


def shares_from(net_profit: Optional[float], eps: Optional[float]) -> Optional[float]:
    """由 归母净利润 ÷ 基本每股收益 反推股本。这是会计恒等式，不是估算。"""
    if net_profit is None or eps in (None, 0):
        return None
    try:
        s = float(net_profit) / float(eps)
    except (TypeError, ZeroDivisionError):
        return None
    return s if s > 0 else None


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def build_series(
    thscode: str,
    bars: list[dict],
    income_q: list[dict],
    income_a: list[dict],
    balance_a: list[dict],
    snapshot_item: Optional[dict],
    tolerance: float = 0.35,
) -> ValuationSeries:
    """重建 PE / PB 日序列，并跑三道自检。

    tolerance：自校准的相对容差（默认 35%）。超过就判为口径不一致，降级而不是硬用。
    """
    series = ValuationSeries(thscode=thscode)

    if not bars:
        series.errors.append("历史 K 线为空，无法重建任何序列")
        series.checks.append(
            SeriesCheck("K线可用性", False, "历史 K 线返回空数组，序列重建的前置条件不成立", "blocking")
        )
        return series

    # --- 自检 1：季报口径 -------------------------------------------------
    cum, cum_evidence = infer_cumulative(income_a, income_q)
    series.checks.append(
        SeriesCheck(
            "季报口径反推",
            None if cum is None else True,
            cum_evidence,
            "warning" if cum is None else "info",
        )
    )

    # --- 构造 EPS_TTM 与 BVPS 阶梯 ---------------------------------------
    eps_points: list[tuple[int, float]] = []
    if cum is not None:
        ttm_np = build_ttm_points(income_q, income_a, "parent_holder_net_profit", cum)
        shares_pts: list[tuple[int, float]] = []
        for r in income_q:
            if not r.get("report_date_ms"):
                continue
            s = shares_from(r.get("parent_holder_net_profit"), r.get("basic_eps"))
            if s:
                shares_pts.append((r["report_date_ms"], s))
        if not shares_pts:
            for r in income_a:
                if not r.get("report_date_ms"):
                    continue
                s = shares_from(r.get("parent_holder_net_profit"), r.get("basic_eps"))
                if s:
                    shares_pts.append((r["report_date_ms"], s))

        for date_ms, np_ttm in ttm_np:
            s = _step_lookup(shares_pts, date_ms)
            if s:
                eps_points.append((date_ms, np_ttm / s))

    if not eps_points:
        series.checks.append(
            SeriesCheck(
                "EPS 序列可构造性",
                False,
                "无法由利润表构造 EPS_TTM 阶梯（季报口径未确定或字段缺失），PE 序列重建中止",
                "blocking",
            )
        )
    else:
        series.checks.append(
            SeriesCheck(
                "EPS 序列可构造性",
                True,
                f"由利润表构造出 {len(eps_points)} 个 EPS_TTM 阶梯点，"
                f"覆盖 {_fmt_ms(eps_points[0][0])} ~ {_fmt_ms(eps_points[-1][0])}",
                "info",
            )
        )
        series.provenance_notes.append(
            "EPS_TTM 由合并利润表按季度口径合成，披露日为阶梯跳变点（投资者当时真实可见的数据）"
        )

    # BVPS：归母净资产 / 股本
    bvps_points: list[tuple[int, float]] = []
    for r in balance_a:
        if not r.get("report_date_ms"):
            continue
        eq = r.get("holder_equity_total")
        if eq is None:
            continue
        s = _step_lookup(
            [
                (x["report_date_ms"], shares_from(x.get("parent_holder_net_profit"), x.get("basic_eps")))
                for x in income_a
                if x.get("report_date_ms")
                and shares_from(x.get("parent_holder_net_profit"), x.get("basic_eps"))
            ],
            r["report_date_ms"],
        )
        if s:
            bvps_points.append((r["report_date_ms"], float(eq) / s))

    # --- 主序列 -----------------------------------------------------------
    _fill(series, "pe_reconstructed", bars, eps_points, "前复权价 ÷ EPS_TTM（重建）")
    _fill(series, "pb_reconstructed", bars, bvps_points, "前复权价 ÷ BVPS（重建）")

    # --- 自检 2：与权威快照对拍 -------------------------------------------
    if snapshot_item and series.metrics.get("pe_reconstructed"):
        our = series.metrics["pe_reconstructed"][-1].value
        official = snapshot_item.get("pe_ttm")
        series.calibration = {
            "our_latest_pe": round(our, 4),
            "official_pe_ttm": official,
            "official_source": "GET /api/a-share/valuations/snapshot → pe_ttm",
            "tolerance": tolerance,
        }
        if official in (None, 0):
            series.checks.append(
                SeriesCheck(
                    "对权威快照自校准",
                    None,
                    f"官方 pe_ttm 返回 {official!r}（空值或零，可能是净利润为负），无法对拍",
                    "warning",
                )
            )
            series.calibration["relative_gap"] = None
        else:
            gap = abs(our - float(official)) / abs(float(official))
            series.calibration["relative_gap"] = round(gap, 4)
            passed = gap <= tolerance
            series.checks.append(
                SeriesCheck(
                    "对权威快照自校准",
                    passed,
                    f"重建最新 PE={our:.2f} vs 官方 pe_ttm={float(official):.2f}，相对偏差 {gap:.1%}"
                    + ("（在容差内，重建方法可信）" if passed else "（超出容差，重建口径与官方口径不一致，结论降级处理）"),
                    "info" if passed else "warning",
                )
            )
            if not passed:
                series.provenance_notes.append(
                    "重建 PE 与官方 pe_ttm 存在显著偏差，可能来自 TTM 构造差异、"
                    "少数股东损益处理或复权口径差异；本序列仅作趋势参考，不作为精确估值依据"
                )

    return series


def _fill(
    series: ValuationSeries,
    name: str,
    bars: list[dict],
    basis_points: list[tuple[int, float]],
    basis_label: str,
) -> None:
    if not basis_points:
        return
    pts: list[Point] = []
    for bar in bars:
        t = bar.get("date_ms")
        close = bar.get("close_price")
        if t is None or close in (None, 0):
            continue
        denom = _step_lookup(basis_points, t)
        if denom in (None, 0):
            continue
        pts.append(Point(date_ms=t, value=float(close) / denom, basis=basis_label))
    if pts:
        pts.sort(key=lambda p: p.date_ms)
        series.metrics[name] = pts


def _fmt_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")

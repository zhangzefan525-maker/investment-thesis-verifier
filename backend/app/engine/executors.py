"""子问题执行器：把 gold 模板里的每条子问题接到真实取数与判定规则上。

约定：
- 每个执行器只读 `Ctx` 里已经取好的数据，**不再发起新的网络请求**。
  取数统一在 `collect.py` 里做一次，失败以 `Err` 形式挂在 Ctx 上。
- 执行器拿到失败时，必须产出 `Verdict.UNVERIFIABLE` 的证据并附带三问详情，
  **不允许返回 None 或静默跳过**。
- 判定阈值与 `templates/gold.py` 里写的规则一一对应。模板是契约，这里是实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..analysis.fundamentals import health_check
from ..analysis.valuation_series import ValuationSeries
from ..providers.base import Err, FetchResult
from ..schemas import (
    Confidence,
    Evidence,
    Provenance,
    UnverifiableCategory,
    UnverifiableDetail,
    Verdict,
)

DAY_MS = 86_400_000


@dataclass
class Ctx:
    """一次验证运行中所有已取到的数据与派生序列。"""

    thscode: str
    ticker: str
    name: str
    series_fwd: Optional[ValuationSeries] = None
    series_none: Optional[ValuationSeries] = None
    bars_fwd: list[dict] = field(default_factory=list)
    bars_none: list[dict] = field(default_factory=list)
    income_q: list[dict] = field(default_factory=list)
    income_a: list[dict] = field(default_factory=list)
    balance_a: list[dict] = field(default_factory=list)
    balance_q: list[dict] = field(default_factory=list)
    cashflow_q: list[dict] = field(default_factory=list)
    cashflow_a: list[dict] = field(default_factory=list)
    indicators: dict[str, Optional[str]] = field(default_factory=dict)
    valuation_snapshot: Optional[dict] = None
    expected_report: str = ""
    # 多期派生序列（按报告期升序）
    gm_series: list[float] = field(default_factory=list)
    cost_ratio_series: list[float] = field(default_factory=list)
    peer_rows: list[dict] = field(default_factory=list)
    # 各路取数的失败记录，键为逻辑名
    failures: dict[str, Err] = field(default_factory=dict)
    # 每路取数的溯源，用于生成 Evidence 的 provenance
    procs: dict[str, Provenance] = field(default_factory=dict)
    run_errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prov(
    ctx: Ctx,
    key: str,
    report_period: str,
    caliber: str,
    unit: str,
    raw: Any = None,
) -> Provenance:
    """从已记录的取数溯源里派生一条 Evidence 级溯源。"""
    base = ctx.procs.get(key)
    if base is None:
        return Provenance(
            source="同花顺扶摇",
            endpoint=f"(未成功取数: {key})",
            report_period=report_period,
            caliber=caliber,
            unit=unit,
            raw={"note": "该路数据未取得，见同条证据的无法验证说明"},
        )
    return base.model_copy(
        update={
            "report_period": report_period,
            "caliber": caliber,
            "unit": unit,
            "raw": raw if isinstance(raw, dict) else {"value": raw},
        }
    )


def _unv(
    ctx: Ctx,
    key: str,
    sub_question_id: str,
    claim: str,
    why_needed: str,
    where: str,
    rule: str,
    threshold: str,
) -> Evidence:
    """由取数失败生成一条无法验证证据。

    这里要分清两种**完全不同**的失败，早先被混成了一种：

    1. 取数路自己失败了（超时、无权限、标的不存在）——`ctx.failures` 里有记录，照实转述。
    2. **取数成功了，但取回来的数据里没有本条子问题要的字段。**

    第 2 种此前落到 else 分支，写出一句「未找到 <key> 的取数记录（既无成功返回也无失败记录）」。
    中国平安的 SQ-06 就是这种：`financial_indicators` 取数完全成功，但它是一份保险公司的
    利润表，「毛利率」这个口径根本不存在（保险公司没有 operating_costs 这一行），
    于是毛利率序列凑不满 4 期。屏幕上于是出现了一句**明显与事实不符**的话，
    而真正的原因（该标的的报表结构里没有这个口径）一个字都没说。
    读者据此会把「这个数据源没有能力」误读成「这次调用出了故障」——
    两者的后续动作完全相反：前者要换数据源，后者重试即可。
    """
    err = ctx.failures.get(key)
    if err is not None:
        detail = err.detail
        endpoint = err.endpoint
    elif key in ctx.procs:
        # 取数成功但字段不可用。用 DATA_NOT_EXIST 而非 SOURCE_UNREACHABLE：
        # 报的是「这个口径在该标的的披露结构里不存在」，不是「数据源连不上」。
        detail = UnverifiableDetail(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            what_is_needed=why_needed,
            where_to_get=where,
            failure_evidence=(
                f"{key} 一路取数成功（见本卡溯源），但返回的数据里没有本条子问题可用的字段。"
                f"本条需要：{why_needed}。失败性质是「该标的的披露口径与本条所需口径不匹配」，"
                f"**不是取数故障**——重试不会有任何改变，需换数据源或换口径。"
            ),
        )
        endpoint = ctx.procs[key].endpoint
    else:
        detail = UnverifiableDetail(
            category=UnverifiableCategory.SOURCE_UNREACHABLE,
            what_is_needed=why_needed,
            where_to_get=where,
            failure_evidence=f"未找到 {key} 的取数记录（既无成功返回也无失败记录）",
        )
        endpoint = f"(missing: {key})"
    # 推理句也要按**是哪一种失败**来写。上一版对三种情形共用一句
    # 「取数路径未能返回可用数据」，于是「取数其实成功了、只是字段口径不合」也被写成取数失败，
    # 与下方三问里的证据自相矛盾。溯源里的 endpoint 此前算了却没有用上，
    # 现在把它接进去：取数失败时，卡上要能看到**失败的是哪个接口**。
    if err is not None:
        reasoning = (
            f"本子问题依赖的取数路径调用失败，因此无法做出支持或反对的判断。"
            f"失败性质：{detail.category.value}。原始证据：{detail.failure_evidence}"
        )
    elif key in ctx.procs:
        reasoning = (
            f"取数路径**调用成功**，但返回的数据不含本条子问题所需的字段，因此无法做出支持或反对的判断。"
            f"失败性质：{detail.category.value}。原始证据：{detail.failure_evidence}"
        )
    else:
        reasoning = (
            f"本子问题依赖的取数路径未能返回可用数据，因此无法做出支持或反对的判断。"
            f"失败性质：{detail.category.value}。原始证据：{detail.failure_evidence}"
        )
    return Evidence(
        id=f"EV-{sub_question_id}",
        sub_question_id=sub_question_id,
        claim=claim,
        value=None,
        display_value="—",
        provenance=prov(ctx, key, "不可得", "不可得", "-").model_copy(
            update={"endpoint": endpoint}
        ),
        decision_rule_applied=rule,
        threshold_applied=threshold,
        verdict=Verdict.UNVERIFIABLE,
        confidence=Confidence.LOW,
        reasoning=reasoning,
        fact_or_logic="fact",
        unverifiable=detail,
    )


def _inconclusive(
    metric: str, value_desc: str, rule: str, why: str
) -> UnverifiableDetail:
    """数据取到了，但值落在判定规则的中性区间——这是一种**有实质内容的**无法验证。

    它与「取数失败」的区别在于：数据没问题，是判定规则本身在这个区间内不做判断。
    因此三问的答案也不同——需要的不是「更多数据」，而是「这个指标走出中性区间」。
    """
    return UnverifiableDetail(
        category=UnverifiableCategory.INCONCLUSIVE_RANGE,
        what_is_needed=f"{metric} 走出判定规则的中性区间，使证据态可从「中性」落向「支持」或「反对」",
        where_to_get=f"下一个报告期的 {metric}；当前值 {value_desc}",
        failure_evidence=(
            f"{metric} 当前值 {value_desc} 落在判定规则「{rule}」定义的中性区间内。"
            f"数据完整、口径一致，但该值对本命题不构成方向性证据——{why}"
        ),
    )


def _mk(
    ctx: Ctx,
    sub_question_id: str,
    claim: str,
    value: Any,
    display: str,
    provenance: Provenance,
    rule: str,
    threshold: str,
    verdict: Verdict,
    confidence: Confidence,
    reasoning: str,
    unverifiable: Optional[UnverifiableDetail] = None,
    fact_or_logic: str = "fact",
) -> Evidence:
    return Evidence(
        id=f"EV-{sub_question_id}",
        sub_question_id=sub_question_id,
        claim=claim,
        value=value,
        display_value=display,
        provenance=provenance,
        decision_rule_applied=rule,
        threshold_applied=threshold,
        verdict=verdict,
        confidence=confidence,
        reasoning=reasoning,
        fact_or_logic=fact_or_logic,
        unverifiable=unverifiable,
    )


def _fmt_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def _latest(items: list[dict]) -> Optional[dict]:
    usable = [i for i in items if i.get("period_end_ms")]
    return max(usable, key=lambda i: i["period_end_ms"]) if usable else None


def _prev_same_period(items: list[dict], latest: dict) -> Optional[dict]:
    y, fp = latest.get("fiscal_year"), latest.get("fiscal_period")
    if y is None or fp is None:
        return None
    for it in items:
        if it.get("fiscal_year") == y - 1 and it.get("fiscal_period") == fp:
            return it
    return None


def _match_period(items: list[dict], ref: Optional[dict]) -> Optional[dict]:
    """在另一张报表里找出与 ref **同一 fiscal_year + fiscal_period** 的那一期。

    跨表比值（如 经营现金流 ÷ 归母净利润）必须同报告期，否则分子分母不同期，
    算出来的不是任何时点的真实比率。找不到对应期就返回 None，由调用方如实报缺失。
    """
    if not ref:
        return None
    y, fp = ref.get("fiscal_year"), ref.get("fiscal_period")
    if y is None or fp is None:
        return None
    for it in items:
        if it.get("fiscal_year") == y and it.get("fiscal_period") == fp:
            return it
    return None


def _pname(item: Optional[dict]) -> str:
    if not item:
        return "—"
    return f"{item.get('fiscal_year')}{item.get('fiscal_period', 'FY')}"


def _latest_comparable(ctx: Ctx) -> tuple[Optional[dict], Optional[dict], str]:
    """取最新可同比的一对报告期。

    在「季报最新一期 vs 上年同期」与「最新年报 vs 上一年报」之间选**更新的那一对**，
    两者都满足同 fiscal_period 的口径一致要求，因此取新的不会牺牲可比性。
    季报数据缺失或上年同期不可比时退回年报。
    """
    for pool, label in ((ctx.income_q, "季报"), (ctx.income_a, "年报")):
        usable = [r for r in pool if r.get("period_end_ms")]
        if not usable:
            continue
        lat = max(usable, key=lambda r: r["period_end_ms"])
        prev = _prev_same_period(pool, lat)
        if prev is not None:
            return lat, prev, label
    lat = _latest(ctx.income_a) or _latest(ctx.income_q)
    return lat, _prev_same_period(ctx.income_a, lat) if lat else None, "年报"


# ==========================================================================
# 背离型执行器
# ==========================================================================


def ex_div_01(ctx: Ctx) -> Evidence:
    """SQ-01 估值分位是否回落。"""
    sq, key = "SQ-01", "prices_historical"
    s: Optional[ValuationSeries] = ctx.series_fwd
    if s is None or not s.metrics.get("pe_reconstructed"):
        return _unv(
            ctx, key, sq,
            "估值历史分位不可计算",
            "一段可用以重建 PE 序列的历史行情与利润表数据",
            "补齐历史 K 线与财务数据后重跑；若重建方法不适用，需接入提供官方历史估值的数据源",
            "计算重建 PE 当前值在自身历史序列中的分位，≤30 判为回落",
            "分位 ≤ 30 / 30–70 / ≥ 70",
        )

    pct = s.percentile("pe_reconstructed")
    if pct is None:
        return _mk(
            ctx, sq,
            "重建 PE 序列样本不足，分位不具统计意义",
            None, "—",
            prov(ctx, key, "—", "前复权价 ÷ EPS_TTM（重建）", "倍"),
            "分位需至少约 60 个交易日样本", "≥ 60 个样本", Verdict.UNVERIFIABLE, Confidence.LOW,
            f"重建序列仅 {len(s.metrics['pe_reconstructed'])} 个点，不足以计算有意义的分位。"
            f"按既有约束，此时不硬算分位，而是判定为无法验证",
            UnverifiableDetail(
                category=UnverifiableCategory.CALIBER_MISMATCH,
                what_is_needed="覆盖至少一个完整市场周期的历史数据",
                where_to_get="该标的上游财务披露期数不足时无法补齐；可改选上市时间更长的标的",
                failure_evidence=f"重建 PE 序列长度 {len(s.metrics['pe_reconstructed'])} < 60",
            ),
        )

    latest_pe = s.metrics["pe_reconstructed"][-1].value
    calib = s.calibration or {}
    gap = calib.get("relative_gap")
    conf = Confidence.MEDIUM if (gap is None or gap > 0.35) else Confidence.HIGH

    # 三态与阈值必须是同一件事。此前这里写作 `... else Verdict.REFUTE`，
    # 30–70 的「中性」被并进了「反对」—— 同一张卡上的 threshold_applied 印着
    # 「30–70 → 中性」，推理句也写「尚无充分证据表明估值回落」，判定却是「反对」。
    # 数据取到了、只是落在中性区间不足以定论，对应的正是 INCONCLUSIVE_RANGE
    # 这个早就定义好、却一直没有被这个执行器用上的分类。
    if pct <= 30:
        verdict = Verdict.SUPPORT
    elif pct >= 70:
        verdict = Verdict.REFUTE
    else:
        verdict = Verdict.UNVERIFIABLE

    calib_note = ""
    if gap is not None:
        calib_note = (
            f"重建最新 PE={calib['our_latest_pe']} 与官方 pe_ttm={calib['official_pe_ttm']} "
            f"相对偏差 {gap:.1%}。" + ("偏差在容差内，重建方法可信。" if gap <= 0.35 else
            "**偏差超出容差**，重建口径与官方口径不一致，本条结论已降级为中等置信度，"
            "只能作趋势参考、不可作精确估值依据。")
        )
    elif calib:
        calib_note = "官方 pe_ttm 为空值（可能因净利润为负），无法完成自校准，本条为中等置信度。"

    if verdict is Verdict.UNVERIFIABLE:
        detail = UnverifiableDetail(
            category=UnverifiableCategory.INCONCLUSIVE_RANGE,
            what_is_needed="分位落入 ≤ 30 或 ≥ 70 的判定区间；或一个覆盖完整周期的参照区间，"
                           "让「中性」本身具备可比含义",
            where_to_get="延长重建序列（需更多历史报告期）后重算，或改用较长的观察窗",
            failure_evidence=f"分位 {pct} 落在 30–70 中性区间，判定规则未给出方向",
        )
    else:
        detail = None

    # 「自身历史」这四个字必须带上窗口。重建序列的长度由可用报告期数决定，
    # 实测只有 361 / 371 个交易日（约 1.4 年）——写「自身历史分位」，
    # 读者会读成「上市以来的历史分位」，而实际上这是最近一年多里的位置。
    # 同一条卡上印着窗口、推理句里也写了样本数，但 claim 是**先被看到**的那一行，
    # 也是被复制进结论、图表页脚的那一行；先入为主的那句话才要最准。
    pts = s.metrics["pe_reconstructed"]
    win = f"{_fmt_ms(pts[0].date_ms)} ~ {_fmt_ms(pts[-1].date_ms)}"
    # 跨度由序列自身的首尾日期算出，不写「244 个交易日/年」这类常数——
    # 常数一旦与实际交易日历不符，读者核对不上，而且没人会发现。
    span_years = (pts[-1].date_ms - pts[0].date_ms) / (365.25 * 24 * 3600 * 1000)
    return _mk(
        ctx, sq,
        f"重建 PE 当前值 {latest_pe:.2f}，处于自有可重建区间（{win}）的 {pct} 分位",
        latest_pe,
        f"{latest_pe:.2f}（{pct} 分位）",
        prov(ctx, key, win,
             "重建口径：前复权收盘价 ÷ EPS_TTM（EPS 按披露日阶梯跳变）", "倍",
             {"percentile": pct, "latest": round(latest_pe, 4)}),
        "计算重建 PE 当前值在自有可重建区间内的分位；分位越低代表估值越靠近该区间的低位。"
        "本产品不提供「自上市以来」的分位——重建序列只覆盖可用于重建的最近若干报告期",
        "≤ 30 分位 → 估值确已回落；30–70 → 中性；≥ 70 → 未回落",
        verdict, conf,
        f"当前重建 PE 为 {latest_pe:.2f}，在自有可重建区间内的 {pct} 分位"
        f"（样本 {len(pts)} 个交易日，跨度 {span_years:.1f} 年）。"
        f"该区间**不是「自上市以来」**，跨度过短时分位的绝对水平只宜作趋势参考。"
        f"{calib_note}"
        f"按判定规则，{pct} 分位{'落在 30 分位以内，估值确已回落' if pct <= 30 else ('处于 30–70 中性区间，尚无充分证据表明估值回落，故本条判为**无法验证**而非反对' if pct < 70 else '处于 70 分位以上，估值并未回落')}。",
        unverifiable=detail,
    )


def ex_div_02(ctx: Ctx) -> Evidence:
    """SQ-02 价格回撤。"""
    sq, key = "SQ-02", "prices_historical"
    bars = ctx.bars_fwd
    if not bars:
        return _unv(
            ctx, key, sq, "价格回撤不可计算",
            "该标的近 3 年的日线行情",
            "扶摇 /api/a-share/prices/historical；若持续失败需检查标的代码与凭据权限",
            "计算区间最大回撤", "回撤 ≤ −20% 判为显著回落",
        )
    recent = [b for b in bars if b.get("close_price") not in (None, 0)]
    if len(recent) < 20:
        return _unv(
            ctx, key, sq, "价格样本过少，回撤不具意义",
            "至少 20 个交易日的收盘价",
            "该标的可能上市时间过短",
            "计算区间最大回撤", "回撤 ≤ −20%",
        )
    hi = max(recent, key=lambda b: b["close_price"])
    last = recent[-1]
    dd = (last["close_price"] - hi["close_price"]) / hi["close_price"]
    days = (last["date_ms"] - hi["date_ms"]) / DAY_MS
    verdict = Verdict.SUPPORT if dd <= -0.20 else Verdict.REFUTE
    return _mk(
        ctx, sq,
        f"自 {_fmt_ms(hi['date_ms'])} 高点以来价格回撤 {dd:.1%}，历时 {days:.0f} 天",
        round(dd, 4), f"{dd:.1%}",
        prov(ctx, key, f"{_fmt_ms(recent[0]['date_ms'])} ~ {_fmt_ms(last['date_ms'])}",
             "前复权收盘价", "CNY",
             {"peak": hi["close_price"], "last": last["close_price"],
              "peak_date": _fmt_ms(hi["date_ms"])}),
        "区间最大回撤 =（最新收盘价 − 区间最高收盘价）÷ 区间最高收盘价",
        "回撤 ≤ −20% → 显著回落；−20%~0% → 温和；> 0 → 未回落",
        verdict, Confidence.HIGH,
        f"区间最高前复权收盘价 {hi['close_price']:.2f}（{_fmt_ms(hi['date_ms'])}），"
        f"最新 {last['close_price']:.2f}（{_fmt_ms(last['date_ms'])}），回撤 {dd:.1%}。"
        f"按规则，{'价格已显著回落' if dd <= -0.20 else '价格回落幅度未达 20% 阈值'}。"
        f"本条与 SQ-01 互为交叉验证：估值回落可能来自价格下跌、也可能来自盈利上升，"
        f"两条一起看才能区分。",
    )


def ex_div_03(ctx: Ctx) -> Evidence:
    """SQ-03 收入端是否恶化。"""
    return _growth_evidence(
        ctx, "SQ-03", "income_statements", "营业收入", "operating_income",
        support_when_positive=True,
        narrative="「基本面未恶化」的第一层直接证据。收入是基本面的起点。",
    )


def ex_div_04(ctx: Ctx) -> Evidence:
    """SQ-04 利润端是否恶化。"""
    return _growth_evidence(
        ctx, "SQ-04", "income_statements", "归母净利润", "parent_holder_net_profit",
        support_when_positive=True,
        narrative="利润是估值分位的分母，也是「基本面」最直接的度量。",
    )


def _growth_evidence(
    ctx: Ctx, sq: str, key: str, label: str, field_name: str,
    support_when_positive: bool, narrative: str,
) -> Evidence:
    """同比证据。**优先用最新一期季报对上年同期**，而不是最新年报。

    理由：口径一致性（同 fiscal_period）与数据新鲜度可以同时满足。
    只用年报会漏掉最近两个季度的经营变化——在「基本面有没有恶化」这个问题上，
    半年前的年度数据可能已经完全过时。两者口径同样一致，那就该取新的那个。
    """
    lat, prev, caliber = _latest_comparable(ctx)
    if lat is None:
        return _unv(
            ctx, key, sq, f"{label}同比不可计算", "至少两期同口径财务数据",
            "扶摇 /api/a-share/financials/income-statements",
            "同 fiscal_period 同比", "≥ 0 为未恶化",
        )
    cur_v = lat.get(field_name)
    prev_v = (prev or {}).get(field_name)
    if prev is None or cur_v is None or prev_v in (None, 0):
        return _unv(
            ctx, key, sq, f"{label}同比不可计算",
            f"{_pname(lat)} 与 {_pname(prev)} 的 {label} 均可取且上期不为零",
            f"上一年同期数据缺失时无法同比（已尝试 {caliber} 口径）；"
            f"可能需要更长的 limit 或改用另一口径序列",
            "同 fiscal_period 同比", "≥ 0 为未恶化",
        )
    g = 100.0 * (cur_v - prev_v) / abs(prev_v)
    verdict = Verdict.SUPPORT if ((g >= 0) == support_when_positive) else Verdict.REFUTE
    return _mk(
        ctx, sq,
        f"{label}同比 {g:+.2f}%（{_pname(lat)} vs {_pname(prev)}）",
        round(g, 2), f"{g:+.2f}%",
        prov(ctx, key, f"{_pname(lat)} vs {_pname(prev)}",
             f"整体合并报表（consolidated）/ 同 fiscal_period 同比 / 取自{caliber}序列",
             "原币元", {"current": cur_v, "previous": prev_v, "series": caliber}),
        "计算同比增速，使用同 fiscal_period 对比以保证口径一致",
        "同比 ≥ 0 → 未恶化；< 0 → 已恶化",
        verdict, Confidence.HIGH,
        f"{_pname(lat)} {label} {cur_v:,.0f} 元，上年同期 {prev_v:,.0f} 元，同比 {g:+.2f}%。"
        f"按规则，{'该侧未恶化' if verdict is Verdict.SUPPORT else '该侧已恶化，命题该侧被证伪'}。{narrative}",
    )


def ex_div_05(ctx: Ctx) -> Evidence:
    """SQ-05 利润的现金含量。"""
    sq, key = "SQ-05", "cash_flow_statements"
    hc = health_check(ctx.income_a, ctx.cashflow_a, ctx.indicators, ctx.expected_report)
    r = hc.get("净利润现金含量")
    if r is None or not r.ok:
        return _unv(
            ctx, key, sq, "净利润现金含量不可计算",
            "同一报告期的经营活动现金流净额与归母净利润",
            "扶摇 /api/a-share/financials/cash-flow-statements 与 income-statements；"
            "若现金流量表为空需检查该标的披露情况",
            "经营现金流净额 ÷ 归母净利润", "≥ 0.8 判为健康",
        )
    v = r.value
    verdict = Verdict.SUPPORT if v >= 0.8 else (Verdict.UNVERIFIABLE if v >= 0.5 else Verdict.REFUTE)
    return _mk(
        ctx, sq,
        f"净利润现金含量 {v:.2f} 倍（报告期 {r.period}）",
        v, f"{v:.2f} 倍",
        prov(ctx, key, r.period, "经营活动现金流净额 ÷ 归母净利润", "倍"),
        "计算比值。长期显著低于 1 说明账面利润没有变成现金", "≥ 0.8 健康；0.5–0.8 需关注；< 0.5 存疑",
        verdict, Confidence.MEDIUM,
        f"{r.period} 净利润现金含量为 {v:.2f} 倍。"
        f"按规则，{'盈利质量健康' if v >= 0.8 else ('处于需要关注的区间，本条不给出确定结论' if v >= 0.5 else '盈利质量存疑，此时即使营收利润为正，「基本面未恶化」的判断也需打折')}。",
        unverifiable=(
            _inconclusive(
                "净利润现金含量", f"{v:.2f} 倍",
                "≥ 0.8 支持；0.5–0.8 中性；< 0.5 反对",
                "该比值落在 0.5–0.8 的过渡带，既不能证明利润已充分变现，也不足以证明盈利质量恶化。"
                "单一报告期的现金含量受结算节奏影响较大，逐期观察比单点判定更可靠",
            )
            if verdict is Verdict.UNVERIFIABLE else None
        ),
    )


def ex_div_06(ctx: Ctx) -> Evidence:
    """SQ-06 周期性陷阱检测。"""
    sq, key = "SQ-06", "financial_indicators"
    gm_series = getattr(ctx, "gm_series", None) or []
    if len(gm_series) < 4:
        return _unv(
            ctx, key, sq, "周期性无法判定",
            "至少 4 个报告期的毛利率序列，以及该标的的行业归属数据",
            "扶摇 /api/a-share/financials/indicators 可逐期取毛利率；"
            "行业景气度与周期位置数据扶摇公开接口不提供",
            "以毛利率的历史波动幅度作为周期性代理指标", "标准差 > 5pp 判为强周期性",
        )
    import statistics

    sd = statistics.pstdev(gm_series)
    cyclical = sd > 5.0
    return _mk(
        ctx, sq,
        f"毛利率最近 {len(gm_series)} 期标准差 {sd:.2f}pp，"
        f"{'提示存在周期性特征' if cyclical else '未见明显周期性特征'}",
        round(sd, 2), f"{sd:.2f}pp",
        prov(ctx, key, f"最近 {len(gm_series)} 期", "毛利率序列离散度（周期性代理）", "pp",
             {"gm_series": [round(x, 2) for x in gm_series]}),
        "以毛利率历史波动幅度作为周期性代理；标准差越大越可能存在周期性",
        "标准差 > 5pp → 强周期性，SQ-01 的 PE 分位须附加周期限定",
        Verdict.UNVERIFIABLE, Confidence.LOW,
        f"毛利率 {len(gm_series)} 期标准差 {sd:.2f}pp。"
        f"{'该指标提示标的存在周期性特征——周期行业的 PE 会在盈利低谷异常走高、盈利高峰异常走低，' if cyclical else '未见明显周期波动——'}"
        f"因此 SQ-01 的 PE 分位{'不可机械读作「低估」，必须结合所处周期位置解读' if cyclical else '可常规解读'}。"
        f"本产品无行业景气度数据，无法确定当前处于周期的哪个位置，故本条判定为无法验证——"
        f"这是一个**有实质内容的无法验证**：它明确告知了结论的适用边界。",
        UnverifiableDetail(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            what_is_needed="标的所属行业及其景气度/周期位置数据（如产能利用率、行业库存、产品价差）",
            where_to_get="扶摇公开接口不覆盖行业景气度与产业链数据；"
                         "需接入提供行业研究的数据源（如同花顺 iFinD 的行业数据模块）或公开行业协会统计",
            failure_evidence="已完成可得部分的计算（毛利率离散度），但周期性判定所需的位置信息在本数据源中不存在",
        ),
    )


# ==========================================================================
# 归因型执行器
# ==========================================================================


def ex_att_01(ctx: Ctx) -> Evidence:
    return _growth_evidence(
        ctx, "SQ-01", "income_statements", "营业收入", "operating_income",
        support_when_positive=True,
        narrative="主营改善的必要条件：收入没增长而利润增长，增量必然来自成本、费用或非经常项目。",
    )


def ex_att_02(ctx: Ctx) -> Evidence:
    """SQ-02 毛利率同比变动。"""
    sq, key = "SQ-02", "income_statements"
    # 与 SQ-01/SQ-04 使用同一对报告期：同一份结论里的数字必须来自同一时点，
    # 否则「营收看最新季报、毛利率看去年年报」会让证据卡自相矛盾。
    lat, prev, caliber = _latest_comparable(ctx)
    if not lat or not prev:
        return _unv(ctx, key, sq, "毛利率同比不可计算", "两期同口径的收入与成本",
                    "扶摇 /api/a-share/financials/income-statements",
                    "计算毛利率及其同比变动（pp）", "> +1pp 支持，< −1pp 反对")
    try:
        gm_c = (lat["operating_income"] - lat["operating_costs"]) / lat["operating_income"] * 100
        gm_p = (prev["operating_income"] - prev["operating_costs"]) / prev["operating_income"] * 100
    except (KeyError, TypeError, ZeroDivisionError):
        return _unv(ctx, key, sq, "毛利率同比不可计算", "两期收入与成本均为有效非零数值",
                    "上游返回存在 null 时不补零，需检查该标的披露完整性",
                    "计算毛利率及其同比变动（pp）", "> +1pp 支持")
    d = gm_c - gm_p
    verdict = Verdict.SUPPORT if d > 1 else (Verdict.REFUTE if d < -1 else Verdict.UNVERIFIABLE)
    return _mk(
        ctx, sq,
        f"毛利率 {gm_c:.2f}%，同比 {d:+.2f}pp（{_pname(lat)} vs {_pname(prev)}，{caliber}口径）",
        round(d, 2), f"{gm_c:.2f}%（{d:+.2f}pp）",
        prov(ctx, key, f"{_pname(lat)} vs {_pname(prev)}",
             f"毛利率 =（营收 − 营业成本）÷ 营收 / 取自{caliber}序列", "%",
             {"gm_current": round(gm_c, 4), "gm_previous": round(gm_p, 4), "series": caliber}),
        "计算毛利率及其同比变动，>+1pp 判为改善，<−1pp 判为恶化，区间内不给确定结论",
        "> +1pp 支持；−1pp ~ +1pp 中性；< −1pp 反对",
        verdict, Confidence.HIGH,
        f"{_pname(lat)} 毛利率 {gm_c:.2f}%，上年同期 {gm_p:.2f}%，变动 {d:+.2f}pp。"
        f"收入增长可能靠降价换来，毛利率是区分「增收又增利」与「增收不增利」的分水岭。"
        f"按规则，{'毛利率确有改善，支持主业改善' if verdict is Verdict.SUPPORT else ('变动幅度在 ±1pp 内，不给出确定结论' if verdict is Verdict.UNVERIFIABLE else '毛利率同比下滑，不支持主业改善')}。",
        unverifiable=(
            _inconclusive(
                "毛利率同比变动", f"{d:+.2f}pp",
                "> +1pp 支持；−1pp ~ +1pp 中性；< −1pp 反对",
                f"变动幅度小于 1pp，既达不到「主业盈利能力改善」的强度，也不构成下滑。"
                f"以 {_pname(lat)} 单季/单期口径看，±1pp 内的波动常由产品结构与季度节奏造成，"
                f"不足以支撑方向性判断",
            )
            if verdict is Verdict.UNVERIFIABLE else None
        ),
    )


def ex_att_03(ctx: Ctx) -> Evidence:
    """SQ-03 非经常性损益（核心判据）。"""
    sq, key = "SQ-03", "financial_indicators"
    roe = ctx.indicators.get("index_weighted_avg_roe")
    roe_d = ctx.indicators.get("index_deduct_weighted_avg_roe")
    if roe is None or roe_d is None:
        return _unv(
            ctx, key, sq, "非经常性损益影响不可计算",
            "同一报告期的加权 ROE 与扣非加权 ROE",
            "扶摇 /api/a-share/financials/indicators；若某项为 null 说明该期未披露",
            "加权ROE − 扣非加权ROE", "> 2pp 判为非主营成分显著",
        )
    try:
        gap = float(roe) - float(roe_d)
    except (TypeError, ValueError):
        return _unv(ctx, key, sq, "非经常性损益影响不可计算", "上游返回可解析的数值",
                    "指标接口返回非数值字符串时无法计算", "加权ROE − 扣非加权ROE", "> 2pp")
    verdict = Verdict.SUPPORT if gap <= 2 else Verdict.REFUTE
    return _mk(
        ctx, sq,
        f"加权ROE − 扣非加权ROE = {gap:+.2f}pp（报告期 {ctx.expected_report}）",
        round(gap, 2), f"{gap:+.2f}pp",
        prov(ctx, key, ctx.expected_report,
             "扣非 ROE 剔除非经常性损益，两者之差即非经常性损益对 ROE 的贡献（代理口径）", "pp",
             {"weighted_roe": roe, "deduct_roe": roe_d}),
        "加权ROE − 扣非加权ROE，差额即非经常性损益的贡献；>2pp 判为非主营成分显著",
        "> 2pp → 反对；≤ 2pp → 支持",
        verdict, Confidence.HIGH,
        f"加权 ROE {roe}%，扣非加权 ROE {roe_d}%，差额 {gap:+.2f}pp。"
        f"这是本数据源下对非经常性损益最直接的可用代理（资产处置、政府补助、公允价值变动等"
        f"都会落在这个差额里）。按规则，"
        f"{'差额在 2pp 以内，利润中非主营成分不显著，支持「改善来自主营业务」' if verdict is Verdict.SUPPORT else '差额超过 2pp，说明利润中有相当部分并非来自可持续的主营经营，命题被削弱'}。",
    )


def ex_att_04(ctx: Ctx) -> Evidence:
    """SQ-04 期间费用率。"""
    sq, key = "SQ-04", "income_statements"
    lat, prev, caliber = _latest_comparable(ctx)
    if not lat or not prev:
        return _unv(ctx, key, sq, "期间费用率不可计算", "两期同口径的收入与三项费用",
                    "扶摇 /api/a-share/financials/income-statements",
                    "（销售+管理+研发）÷ 营收 及其变动", "降幅 >1pp 且收入未增长 → 反对")
    def er(it: dict) -> Optional[float]:
        try:
            return (it["sales_fee"] + it["manage_fee"] + it["research_and_development_expenses"]) / it["operating_income"] * 100
        except (KeyError, TypeError, ZeroDivisionError):
            return None
    c, p = er(lat), er(prev)
    if c is None or p is None:
        return _unv(ctx, key, sq, "期间费用率不可计算", "三项费用与营收均为有效数值（不补零）",
                    "上游存在 null 字段时无法计算该比值", "（销售+管理+研发）÷ 营收", "降幅 >1pp")
    d = c - p
    try:
        rev_g = (lat["operating_income"] - prev["operating_income"]) / abs(prev["operating_income"]) * 100
    except (KeyError, TypeError, ZeroDivisionError):
        rev_g = None

    if d < -1 and rev_g is not None and rev_g <= 0:
        verdict, why = Verdict.REFUTE, "费用率下降但收入并未增长——是省出来的，不是赚出来的"
    elif d < -1 and rev_g is not None and rev_g > 0:
        verdict, why = Verdict.SUPPORT, "费用率下降且收入同步增长——规模效应，支持主业改善"
    elif d >= -1:
        verdict, why = Verdict.UNVERIFIABLE, "费用率未显著下降，本条对命题不构成方向性证据"
    else:
        verdict, why = Verdict.UNVERIFIABLE, "情形不落在既定判据内，不给出确定结论"

    if verdict is Verdict.UNVERIFIABLE and d >= -1:
        detail = _inconclusive(
            "期间费用率同比变动", f"{d:+.2f}pp",
            "降幅 >1pp 才构成方向性证据；否则本条为中性",
            "费用率没有下降，说明利润改善并非由费用压缩贡献——这本可以用来排除「省出来」的解释，"
            "但中性区间本身不支持「改善来自主营业务」这一正面主张，"
            "只能作为背景事实，不能计入支持",
        )
    else:
        detail = _inconclusive(
            "期间费用率同比变动与收入增速的组合", f"{d:+.2f}pp / 收入 {rev_g}",
            "降幅 >1pp 且收入未增长 → 反对；降幅 >1pp 且收入增长 → 支持",
            "费用率降幅超过 1pp（指向「省出来的」），但同期收入增速不可得，"
            "无法区分规模效应与被动降本，两种解释指向相反结论，故不给确定判断",
        )

    return _mk(
        ctx, sq,
        f"期间费用率 {c:.2f}%，同比 {d:+.2f}pp（收入同比 {rev_g:+.2f}%）" if rev_g is not None
        else f"期间费用率 {c:.2f}%，同比 {d:+.2f}pp",
        round(d, 2), f"{c:.2f}%（{d:+.2f}pp）",
        prov(ctx, key, f"{_pname(lat)} vs {_pname(prev)}",
             f"（销售费用+管理费用+研发费用）÷ 营业收入 / 取自{caliber}序列", "%",
             {"er_current": round(c, 4), "er_previous": round(p, 4), "rev_growth": rev_g,
              "series": caliber}),
        "费用率下降>1pp 且收入增速≤0 判为「省出来的」；费用率下降但收入同步增长判为规模效应",
        "降幅 >1pp 且收入未增长 → 反对；降幅 >1pp 且收入增长 → 支持",
        verdict, Confidence.MEDIUM,
        f"{_pname(lat)} 期间费用率 {c:.2f}%，上年同期 {p:.2f}%，变动 {d:+.2f}pp"
        f"{f'，同期收入同比 {rev_g:+.2f}%' if rev_g is not None else ''}。{why}。"
        f"「降本增效」常被当作主业改善来叙事，但它和「主业变强」是两回事，本条专门把两者分开。",
        unverifiable=detail if verdict is Verdict.UNVERIFIABLE else None,
    )


def ex_att_05(ctx: Ctx) -> Evidence:
    """SQ-05 利润现金含量。"""
    sq, key = "SQ-05", "cash_flow_statements"
    lat_i = _latest(ctx.income_a)
    # 现金流必须与利润表**同一报告期**才可比。此前两者各取各的最新一期，
    # 在跨期披露不一致时会把 2026Q2 的现金流除以 2025FY 的利润——分子分母不同期。
    lat_c = _match_period(ctx.cashflow_a, lat_i) if lat_i else None
    if not lat_i or not lat_c or lat_c.get("act_cash_flow_net") is None or not lat_i.get("parent_holder_net_profit"):
        return _unv(ctx, key, sq, "净利润现金含量不可计算",
                    "同一报告期的经营活动现金流净额与归母净利润",
                    "扶摇 /api/a-share/financials/cash-flow-statements；"
                    "若两表报告期不一致则无法配对，需检查该标的披露进度",
                    "经营现金流净额 ÷ 归母净利润", "≥0.8 支持")
    v = lat_c["act_cash_flow_net"] / lat_i["parent_holder_net_profit"]
    verdict = Verdict.SUPPORT if v >= 0.8 else (Verdict.UNVERIFIABLE if v >= 0.5 else Verdict.REFUTE)
    return _mk(
        ctx, sq, f"净利润现金含量 {v:.2f} 倍", round(v, 3), f"{v:.2f} 倍",
        prov(ctx, key, _pname(lat_c), "经营活动现金流净额 ÷ 归母净利润（同报告期配对）", "倍",
             {"ocf": lat_c["act_cash_flow_net"], "net_profit_parent": lat_i["parent_holder_net_profit"],
              "period": _pname(lat_c)}),
        "经营现金流净额 ÷ 归母净利润", "≥0.8 支持；0.5–0.8 中性；<0.5 反对",
        verdict, Confidence.MEDIUM,
        f"{_pname(lat_c)} 经营活动现金流净额 {lat_c['act_cash_flow_net']:,.0f} 元，"
        f"归母净利润 {lat_i['parent_holder_net_profit']:,.0f} 元，比值 {v:.2f}。"
        f"真正的主营改善会带来现金流入；利润增长而现金流恶化通常指向应收账款激增或收入确认激进。",
        unverifiable=(
            _inconclusive(
                "净利润现金含量", f"{v:.2f} 倍",
                "≥0.8 支持；0.5–0.8 中性；<0.5 反对",
                "比值落在 0.5–0.8 的过渡带。这一条的作用是「交叉印证利润真实性」——"
                "在该区间内它既不印证也不推翻，故不计入支持也不计入反对",
            )
            if verdict is Verdict.UNVERIFIABLE else None
        ),
    )


def ex_att_06(ctx: Ctx) -> Evidence:
    """SQ-06 少数股东损益占比。"""
    sq, key = "SQ-06", "income_statements"
    lat = _latest(ctx.income_a)
    if not lat or lat.get("net_profit") in (None, 0) or lat.get("parent_holder_net_profit") is None:
        return _unv(ctx, key, sq, "少数股东损益占比不可计算", "同一报告期的净利润与归母净利润",
                    "扶摇 /api/a-share/financials/income-statements",
                    "(净利润 − 归母净利润) ÷ 净利润", "≥5% 反对")
    v = 100.0 * (lat["net_profit"] - lat["parent_holder_net_profit"]) / lat["net_profit"]
    verdict = Verdict.SUPPORT if v < 5 else Verdict.REFUTE
    return _mk(
        ctx, sq, f"少数股东损益占净利润 {v:.2f}%", round(v, 2), f"{v:.2f}%",
        prov(ctx, key, _pname(lat), "(净利润 − 归母净利润) ÷ 净利润", "%",
             {"net_profit": lat["net_profit"], "parent": lat["parent_holder_net_profit"]}),
        "(净利润 − 归母净利润) ÷ 净利润", "<5% 支持；≥5% 反对",
        verdict, Confidence.HIGH,
        f"{_pname(lat)} 合并净利润 {lat['net_profit']:,.0f} 元，归母净利润 "
        f"{lat['parent_holder_net_profit']:,.0f} 元，少数股东占 {v:.2f}%。"
        f"看合并报表的净利润增长就下结论是常见错误——增量可能主要归子公司少数股东。",
    )


def ex_att_07(ctx: Ctx) -> Evidence:
    """SQ-07 量价拆分——结构性做不到，永久无法验证。"""
    return _mk(
        ctx, "SQ-07",
        "收入增长的量价拆分在本数据源下不可计算",
        None, "—",
        Provenance(
            source="同花顺扶摇（能力边界声明）",
            endpoint="—",
            report_period="—",
            caliber="本数据源不含产销量与分产品单价",
            unit="—",
            raw={"scope_note": "扶摇公开接口覆盖 A股行情/财报/估值/指数板块/基金/期货期权等，不含产销量明细"},
        ),
        "收入 = 销量 × 单价，需要两者分别可得才能拆分",
        "不可判定",
        Verdict.UNVERIFIABLE, Confidence.HIGH,
        "本条不是「取数失败」，而是**结构性不可得**：扶摇公开接口的能力范围内不包含产销量与"
        "分产品单价数据，因此 volume 与 rate 两层在本数据源下永久无法分离。"
        "按既有约束，此时如实声明维度跳过，而不是用「量价齐升」这类无数据支撑的措辞带过。",
        UnverifiableDetail(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            what_is_needed="分产品的产销量与销售单价（或可推导的量价指数）",
            where_to_get="需接入提供产销量明细的数据源，如同花顺 iFinD 的公司深度资料模块，"
                         "或上市公司年报「经营情况讨论与分析」章节中披露的产销量表（巨潮资讯可免费获取原文）",
            failure_evidence="本数据源能力边界声明中不含产销量/分产品单价字段；"
                             "已核查利润表、财务指标两类接口的全部返回字段，均无该维度",
        ),
    )


# ==========================================================================
# 传导型执行器
# ==========================================================================


def ex_tr_01(ctx: Ctx) -> Evidence:
    return _mk(
        ctx, "SQ-01",
        "标的的主营业务构成数据在本数据源下不可得",
        None, "—",
        Provenance(
            source="同花顺扶摇（能力边界声明）",
            endpoint="—", report_period="—",
            caliber="本数据源不含分部收入/主营构成",
            unit="—",
            raw={"scope_note": "已核查：利润表接口返回合并口径总营收与总成本，无分部字段"},
        ),
        "需要分部收入占比才能确定标的在产业链中的位置", "不可判定",
        Verdict.UNVERIFIABLE, Confidence.HIGH,
        "不知道公司靠什么赚钱，就无从谈起「上游涨价会影响它」。利润表接口只返回合并口径的"
        "总营业收入与总营业成本，没有分部收入字段。这一条不成立时，后面所有传导推理都建立在猜测上。",
        UnverifiableDetail(
            category=UnverifiableCategory.NOT_DISCLOSED,
            what_is_needed="按业务/产品/地区拆分的分部收入与毛利",
            where_to_get="上市公司年报「分部报告」附注（巨潮资讯免费原文）；"
                         "或同花顺 iFinD 的公司主营构成数据",
            failure_evidence="扶摇利润表接口字段清单中无分部收入字段；该数据在年报附注中披露但不在结构化接口中",
        ),
    )


def ex_tr_02(ctx: Ctx) -> Evidence:
    """SQ-02 成本端是否变化（营业成本率多期序列）。"""
    sq, key = "SQ-02", "income_statements"
    series = getattr(ctx, "cost_ratio_series", None) or []
    if len(series) < 4:
        return _unv(ctx, key, sq, "营业成本率序列不可计算", "至少 4 个报告期的收入与成本",
                    "扶摇 /api/a-share/financials/income-statements（period=quarterly, limit=8）",
                    "成本率变动绝对值 >3pp 判为成本端确有变化", "> 3pp")
    delta = series[-1] - series[0]
    verdict = Verdict.SUPPORT if abs(delta) > 3 else Verdict.REFUTE
    return _mk(
        ctx, sq,
        f"营业成本率从 {series[0]:.2f}% 变为 {series[-1]:.2f}%，累计变动 {delta:+.2f}pp",
        round(delta, 2), f"{delta:+.2f}pp",
        prov(ctx, key, f"最近 {len(series)} 期", "营业成本 ÷ 营业收入", "%",
             {"series": [round(x, 2) for x in series]}),
        "观察成本率的变化幅度与方向；绝对值 >3pp 判为成本端确有显著变化",
        "|变动| > 3pp → 支持；≤ 3pp → 反对",
        verdict, Confidence.LOW,
        f"营业成本率在最近 {len(series)} 期从 {series[0]:.2f}% 变化到 {series[-1]:.2f}%，"
        f"累计 {delta:+.2f}pp。这是唯一能从公开报表间接观测到的「上游影响」，但它混杂了"
        f"产品结构变化，**无法区分是原材料涨价还是产品结构变化**，因此本条只给低置信度。",
    )


def ex_tr_03(ctx: Ctx) -> Evidence:
    """SQ-03 成本转嫁能力：成本率与毛利率的同向性。"""
    sq, key = "SQ-03", "income_statements"
    cost_series = getattr(ctx, "cost_ratio_series", None) or []
    gm_series = getattr(ctx, "gm_series", None) or []
    if len(cost_series) < 4 or len(gm_series) < 4 or len(cost_series) != len(gm_series):
        return _unv(ctx, key, sq, "成本转嫁能力不可判定", "等长的成本率与毛利率多期序列",
                    "扶摇 /api/a-share/financials/income-statements",
                    "观察成本率上升时毛利率是否同向下降", "毛利率下降 >1pp 判为无转嫁能力")
    d_cost = cost_series[-1] - cost_series[0]
    d_gm = gm_series[-1] - gm_series[0]
    if d_cost > 0 and d_gm < -1:
        verdict, why = Verdict.REFUTE, "成本率上升同时毛利率下降，说明成本压力由公司自行承担，不具备向下游转嫁的能力"
    elif d_cost > 0 and d_gm >= 0:
        verdict, why = Verdict.SUPPORT, "成本率上升但毛利率保持稳定或提升，说明公司有能力提价转嫁成本，具备定价权"
    else:
        verdict, why = Verdict.UNVERIFIABLE, "成本率未上升，本命题预设的传导链条前半段不成立，本条不构成方向性证据"
    return _mk(
        ctx, sq,
        f"成本率变动 {d_cost:+.2f}pp，毛利率变动 {d_gm:+.2f}pp",
        None, f"成本率 {d_cost:+.2f}pp / 毛利率 {d_gm:+.2f}pp",
        prov(ctx, key, f"最近 {len(cost_series)} 期", "成本率与毛利率的同向性", "pp",
             {"cost_delta": round(d_cost, 3), "gm_delta": round(d_gm, 3)}),
        "成本率上升且毛利率同向下降 >1pp → 无转嫁能力；成本率上升但毛利率稳定/上升 → 有转嫁能力",
        "成本率↑ + 毛利率↓ → 反对；成本率↑ + 毛利率稳/升 → 支持",
        verdict, Confidence.MEDIUM,
        f"最近 {len(cost_series)} 期，营业成本率累计变动 {d_cost:+.2f}pp，"
        f"毛利率累计变动 {d_gm:+.2f}pp。{why}。"
        f"「利润分配」这个词的含义就是谁能把成本转嫁给谁，毛利率与成本率的对应关系是"
        f"定价权最直接的报表证据。",
        unverifiable=(
            _inconclusive(
                "营业成本率变动方向", f"{d_cost:+.2f}pp（区间内）",
                "成本率必须**上升**且毛利率下降 >1pp 才判为无转嫁能力；成本率未上升则链条不成立",
                "观测窗口内营业成本率没有上升（甚至下降），意味着上游成本压力这个前提本身不成立。"
                "命题问的是「成本上升时公司能否转嫁」，而窗口内没有发生成本上升，"
                "因此这条证据无法回答该问题——不是数据缺失，是命题前提在观测窗口内未出现",
            )
            if verdict is Verdict.UNVERIFIABLE else None
        ),
    )


def ex_tr_04(ctx: Ctx) -> Evidence:
    """SQ-04 同业对照——找得到与找不到，都要说清楚。"""
    peers = getattr(ctx, "peer_rows", None) or []
    if not peers:
        return _mk(
            ctx, "SQ-04",
            "同业对照组不可构建",
            None, "—",
            Provenance(
                source="同花顺扶摇（能力边界声明）",
                endpoint="—", report_period="—",
                caliber="缺少按行业批量取财务数据的路径",
                unit="—",
                raw={"checked": "已核查 /api/a-share/financials/* 系列接口的入参契约"},
            ),
            "需要同行业可比公司清单 + 批量财务数据", "不可判定",
            Verdict.UNVERIFIABLE, Confidence.HIGH,
            "判断一个成本变化是行业性的还是个股特异的，必须找同业对照。"
            "但扶摇的财务接口全部是**单标的**入参（`thscode` 不接受逗号），"
            "且没有按行业/板块批量取财务指标的路径——批量拉取整个行业既慢又不属于有界请求。"
            "因此同业对照组无法构建。独家好或独家坏含义完全不同，缺了对照组就无法区分。",
            UnverifiableDetail(
                category=UnverifiableCategory.DATA_NOT_EXIST,
                what_is_needed="同行业可比公司清单 + 这些公司的同期利润率数据",
                where_to_get="扶摇的指数/板块接口可提供板块成分股清单（可先取清单），"
                             "但财务指标需逐标的单独请求；"
                             "更经济的路径是接入具备行业横向对比能力的数据源（如 iFinD 行业数据）",
                failure_evidence="/api/a-share/financials/* 的 thscode 参数契约明确为「单只标的，不接受逗号」；"
                                 "无按行业批量取财务的接口",
            ),
        )
    return _mk(
        ctx, "SQ-04", "同业对照组已构建", None, "—",
        prov(ctx, "income_statements", "—", "同业对照", "%"),
        "对比同业成本率变动", "—", Verdict.SUPPORT, Confidence.MEDIUM,
        "同业对照组已构建，详见对照表。",
    )


def ex_tr_05(ctx: Ctx) -> Evidence:
    """SQ-05 外部变量本身是否可得——传导型命题的根节点。"""
    return _mk(
        ctx, "SQ-05",
        "命题所依赖的产业链/宏观外部变量在本数据源中不可观测",
        None, "—",
        Provenance(
            source="同花顺扶摇（能力边界声明）",
            endpoint="—", report_period="—",
            caliber="本数据源不含宏观与产业链价格数据",
            unit="—",
            raw={
                "covered": ["A股行情快照/历史K线", "财务报表与指标", "估值快照",
                            "指数与板块", "集合竞价", "公募基金", "期货期权", "特色数据"],
                "not_covered": ["宏观数据", "产业链价格", "行业供需", "新闻公告原文", "研报"],
            },
        ),
        "需要产销存、原材料价格、下游需求等外部变量的结构化数据", "不可判定",
        Verdict.UNVERIFIABLE, Confidence.HIGH,
        "这是本类命题的根节点。扶摇的能力边界明确列出覆盖范围（A股行情、财务报表与指标、估值、"
        "指数板块、集合竞价、基金、期货期权、特色数据），**不含**宏观数据、产业链价格、行业供需。"
        "外部变量本身取不到时，任何「传导」结论都只能是叙事而非验证——"
        "**把一个不可验证的命题诚实地标为不可验证，比编一段似是而非的产业分析更有价值**。",
        UnverifiableDetail(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            what_is_needed="命题所涉外部变量（如上游原材料价格指数、下游需求增速）的结构化时间序列",
            where_to_get="同花顺 iFinD 的行业与宏观数据模块；"
                         "公开可得路径：国家统计局、行业协会月报、期货交易所结算价（若标的相关品种已上市）",
            failure_evidence="扶摇官方能力边界声明中明确列出不含宏观数据与产业链数据；"
                             "已核查其业务域清单，无对应接口",
        ),
    )


# ==========================================================================
# 注册表：子问题 ID → 执行器
# ==========================================================================

DIVERGENCE_EXECUTORS = {
    "SQ-01": ex_div_01,
    "SQ-02": ex_div_02,
    "SQ-03": ex_div_03,
    "SQ-04": ex_div_04,
    "SQ-05": ex_div_05,
    "SQ-06": ex_div_06,
}

ATTRIBUTION_EXECUTORS = {
    "SQ-01": ex_att_01,
    "SQ-02": ex_att_02,
    "SQ-03": ex_att_03,
    "SQ-04": ex_att_04,
    "SQ-05": ex_att_05,
    "SQ-06": ex_att_06,
    "SQ-07": ex_att_07,
}

TRANSMISSION_EXECUTORS = {
    "SQ-01": ex_tr_01,
    "SQ-02": ex_tr_02,
    "SQ-03": ex_tr_03,
    "SQ-04": ex_tr_04,
    "SQ-05": ex_tr_05,
}

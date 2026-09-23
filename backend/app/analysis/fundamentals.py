"""基本面健康度与盈利归因分解。

两件事：
1. `health_check` —— 回答「基本面恶化了没有」。服务于**背离型**命题。
2. `attribute_earnings` —— 回答「盈利改善是不是来自主营业务」。服务于**归因型**命题。

## 分解层的选择与显式跳过

Anthropic financial-statements skill 给的七类差异来源是：
volume / rate / mix / new-discontinued / one-time / timing / FX。

扶摇公开接口**不提供**产销量、分产品单价、分部收入、外币敞口，
所以 volume / rate / mix / FX 这四层**在本数据源下做不到**。
我们不去用「近似」糊弄，而是把它们显式放进 skipped_layers 并写明原因——
这正对应 longbridge skill 的「维度跳过须显式声明」。

能做到的层：
- revenue_growth    收入增速（volume×rate 的合成，无法再分离）
- gross_margin      毛利率变动
- expense_ratio     期间费用率变动
- non_recurring     非经常性损益（用 加权ROE − 扣非加权ROE 做代理，这是可解释的恒等式差）
- minority_interest 少数股东损益占比
- cash_quality      利润的现金含量
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..schemas import DecompositionLayer


@dataclass
class Ratio:
    name: str
    value: Optional[float]
    unit: str
    period: str
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass
class HealthCheck:
    """基本面健康度。每一项都带报告期，便于「来源可溯」。"""

    ratios: list[Ratio] = field(default_factory=list)
    trend_notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def get(self, name: str) -> Optional[Ratio]:
        for r in self.ratios:
            if r.name == name:
                return r
        return None


@dataclass
class AttributionLayer:
    layer: DecompositionLayer
    label: str
    value: Optional[float]
    unit: str
    contribution_note: str
    direction: str  # 指向主营业务 / 指向非主营 / 中性 / 不可判定


@dataclass
class AttributionResult:
    layers: list[AttributionLayer] = field(default_factory=list)
    skipped_layers: list[DecompositionLayer] = field(default_factory=list)
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    verdict_hint: str = ""


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _pct(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    if part is None or whole in (None, 0):
        return None
    return 100.0 * float(part) / float(whole)


def _ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den in (None, 0):
        return None
    return float(num) / float(den)


def _fmt_period(item: dict) -> str:
    return f"{item.get('fiscal_year')}{item.get('fiscal_period', 'FY')}"


def _latest(items: list[dict]) -> Optional[dict]:
    usable = [i for i in items if i.get("period_end_ms")]
    return max(usable, key=lambda i: i["period_end_ms"]) if usable else None


def _prev_year(items: list[dict], latest: dict) -> Optional[dict]:
    """同口径的上一年同期。年报对年报、季报对季报——口径一致性优先。"""
    y, fp = latest.get("fiscal_year"), latest.get("fiscal_period")
    if y is None or fp is None:
        return None
    for it in items:
        if it.get("fiscal_year") == y - 1 and it.get("fiscal_period") == fp:
            return it
    return None


# --------------------------------------------------------------------------
# 1. 基本面健康度（背离型命题用）
# --------------------------------------------------------------------------


def health_check(
    income: list[dict], cashflow: list[dict], indicators_latest: dict, report_label: str
) -> HealthCheck:
    hc = HealthCheck()
    lat = _latest(income)
    if lat is None:
        hc.skipped.append("利润表无有效报告期，基本面健康度整体不可判定")
        return hc

    prev = _prev_year(income, lat)
    cur_p = _fmt_period(lat)

    # 收入与利润的同比
    for field_name, label in [
        ("operating_income", "营业收入"),
        ("operating_profit", "营业利润"),
        ("parent_holder_net_profit", "归母净利润"),
    ]:
        cur_v, prev_v = lat.get(field_name), (prev or {}).get(field_name)
        if cur_v is not None and prev_v not in (None, 0):
            hc.ratios.append(
                Ratio(f"{label}同比", round(100.0 * (cur_v - prev_v) / abs(prev_v), 2), "%", cur_p)
            )
        else:
            hc.ratios.append(
                Ratio(f"{label}同比", None, "%", cur_p, "缺上一年同期可比数（同 fiscal_period）")
            )

    # 毛利率
    gm = _pct(
        (lat.get("operating_income") or 0) - (lat.get("operating_costs") or 0)
        if lat.get("operating_income") is not None and lat.get("operating_costs") is not None
        else None,
        lat.get("operating_income"),
    )
    hc.ratios.append(Ratio("毛利率", None if gm is None else round(gm, 2), "%", cur_p))

    # 期间费用率（销售+管理+研发）
    exp_parts = [
        lat.get("sales_fee"),
        lat.get("manage_fee"),
        lat.get("research_and_development_expenses"),
    ]
    if all(p is not None for p in exp_parts):
        hc.ratios.append(
            Ratio("期间费用率", round(_pct(sum(exp_parts), lat.get("operating_income")) or 0, 2), "%", cur_p)
        )
    else:
        hc.ratios.append(Ratio("期间费用率", None, "%", cur_p, "费用字段存在 null，不补零"))

    # 非经常性损益代理：加权ROE − 扣非加权ROE
    roe = indicators_latest.get("index_weighted_avg_roe")
    roe_d = indicators_latest.get("index_deduct_weighted_avg_roe")
    if roe is not None and roe_d is not None:
        try:
            gap = float(roe) - float(roe_d)
            hc.ratios.append(
                Ratio(
                    "非经常性损益影响(ROE口径)",
                    round(gap, 2),
                    "pp",
                    report_label,
                    "加权ROE − 扣非加权ROE，差额越大说明非经常性损益对利润贡献越大",
                )
            )
        except (TypeError, ValueError):
            hc.ratios.append(Ratio("非经常性损益影响(ROE口径)", None, "pp", report_label, "上游返回非数值"))
    else:
        hc.ratios.append(
            Ratio("非经常性损益影响(ROE口径)", None, "pp", report_label, "扣非 ROE 或加权 ROE 缺失")
        )

    # 少数股东损益占比
    np_all, np_parent = lat.get("net_profit"), lat.get("parent_holder_net_profit")
    if np_all not in (None, 0) and np_parent is not None:
        hc.ratios.append(
            Ratio("少数股东损益占比", round(100.0 * (np_all - np_parent) / np_all, 2), "%", cur_p)
        )

    # 利润的现金含量
    cf = _latest(cashflow)
    if cf and cf.get("act_cash_flow_net") is not None and np_parent not in (None, 0):
        hc.ratios.append(
            Ratio(
                "净利润现金含量",
                round(float(cf["act_cash_flow_net"]) / float(np_parent), 2),
                "倍",
                _fmt_period(cf),
                "经营活动现金流净额 ÷ 归母净利润；长期显著小于 1 说明利润未转化为现金",
            )
        )
    else:
        hc.ratios.append(Ratio("净利润现金含量", None, "倍", "-", "现金流量表或归母净利润缺失"))

    # 趋势描述
    rev = hc.get("营业收入同比")
    prof = hc.get("归母净利润同比")
    if rev and prof and rev.value is not None and prof.value is not None:
        if rev.value > 0 and prof.value > 0:
            hc.trend_notes.append("收入与利润同比均为正")
        elif rev.value > 0 > prof.value:
            hc.trend_notes.append("收入增长但利润下滑——增收不增利，需查成本或费用")
        elif rev.value < 0 and prof.value > 0:
            hc.trend_notes.append("收入下滑但利润增长——可能来自成本改善或非经常性损益，需归因")
        else:
            hc.trend_notes.append("收入与利润同比均为负")

    return hc


# --------------------------------------------------------------------------
# 2. 盈利归因分解（归因型命题用）
# --------------------------------------------------------------------------

# 扶摇公开接口无法支撑的分解层。不去近似，显式声明。
UNSUPPORTED_LAYERS: dict[DecompositionLayer, str] = {
    DecompositionLayer.VOLUME: "扶摇公开接口不提供产销量数据，无法把收入增长拆成量的贡献",
    DecompositionLayer.RATE: "扶摇公开接口不提供分产品单价，无法把收入增长拆成价的贡献",
    DecompositionLayer.MIX: "扶摇公开接口不提供分部收入，无法拆解业务结构变化（mix）的影响",
    DecompositionLayer.FX: "扶摇公开接口不提供外币敞口与汇兑损益明细，无法拆解汇率影响",
}


def attribute_earnings(
    income: list[dict], indicators_latest: dict, report_label: str
) -> AttributionResult:
    """把盈利变化归因到「主营业务 / 非主营」两侧。"""
    res = AttributionResult()
    res.skipped_layers = list(UNSUPPORTED_LAYERS.keys())
    res.skipped_reasons = {k.value: v for k, v in UNSUPPORTED_LAYERS.items()}

    lat = _latest(income)
    if lat is None:
        res.verdict_hint = "利润表无有效报告期，归因不可判定"
        return res

    prev = _prev_year(income, lat)
    cur_p = _fmt_period(lat)

    # 层 1：收入增速 —— 主营规模是否在扩张
    cur_rev, prev_rev = lat.get("operating_income"), (prev or {}).get("operating_income")
    rev_g = (
        round(100.0 * (cur_rev - prev_rev) / abs(prev_rev), 2)
        if cur_rev is not None and prev_rev not in (None, 0)
        else None
    )
    res.layers.append(
        AttributionLayer(
            DecompositionLayer.REVENUE_GROWTH,
            "营业收入增速",
            rev_g,
            "%",
            "主营规模扩张的直接证据。收入正增长意味着利润改善至少有主营规模支撑",
            "指向主营业务" if (rev_g or 0) > 0 else ("指向非主营" if rev_g is not None else "不可判定"),
        )
    )

    # 层 2：毛利率 —— 主营盈利能力的质量
    gm_cur = _pct(
        (cur_rev - lat["operating_costs"])
        if cur_rev is not None and lat.get("operating_costs") is not None
        else None,
        cur_rev,
    )
    gm_prev = None
    if prev and prev.get("operating_income") is not None and prev.get("operating_costs") is not None:
        gm_prev = _pct(prev["operating_income"] - prev["operating_costs"], prev["operating_income"])
    gm_delta = round(gm_cur - gm_prev, 2) if (gm_cur is not None and gm_prev is not None) else None
    res.layers.append(
        AttributionLayer(
            DecompositionLayer.GROSS_MARGIN,
            "毛利率变动",
            gm_delta,
            "pp",
            f"本期 {gm_cur:.2f}% vs 上年同期 {gm_prev:.2f}%（{cur_p}）"
            if gm_cur is not None and gm_prev is not None
            else "缺可比期毛利率",
            "指向主营业务" if (gm_delta or 0) > 0 else ("指向非主营" if gm_delta is not None else "不可判定"),
        )
    )

    # 层 3：期间费用率 —— 利润改善是否只是省出来的
    def _exp_ratio(item: Optional[dict]) -> Optional[float]:
        if not item:
            return None
        parts = [
            item.get("sales_fee"),
            item.get("manage_fee"),
            item.get("research_and_development_expenses"),
        ]
        if not all(p is not None for p in parts) or item.get("operating_income") in (None, 0):
            return None
        return _pct(sum(parts), item["operating_income"])

    er_cur, er_prev = _exp_ratio(lat), _exp_ratio(prev)
    er_delta = round(er_cur - er_prev, 2) if (er_cur is not None and er_prev is not None) else None
    res.layers.append(
        AttributionLayer(
            DecompositionLayer.EXPENSE_RATIO,
            "期间费用率变动",
            er_delta,
            "pp",
            "费用率下降会推高利润，但这属于经营效率改善而非收入端驱动；需与收入增速合并看",
            "中性（效率改善）" if er_delta is not None and er_delta < 0 else "指向非主营",
        )
    )

    # 层 4：非经常性损益 —— 关键判据
    roe = indicators_latest.get("index_weighted_avg_roe")
    roe_d = indicators_latest.get("index_deduct_weighted_avg_roe")
    nr = None
    if roe is not None and roe_d is not None:
        try:
            nr = round(float(roe) - float(roe_d), 2)
        except (TypeError, ValueError):
            nr = None
    res.layers.append(
        AttributionLayer(
            DecompositionLayer.NON_RECURRING,
            "非经常性损益影响（加权ROE − 扣非加权ROE）",
            nr,
            "pp",
            "这是判断「盈利改善是否来自主营业务」最直接的一层。差额显著为正说明利润里有相当部分"
            "不来自可持续的主营经营",
            "指向非主营" if nr is not None and nr > 2 else ("指向主营业务" if nr is not None else "不可判定"),
        )
    )

    # 层 5：少数股东损益
    np_all, np_parent = lat.get("net_profit"), lat.get("parent_holder_net_profit")
    mi = round(100.0 * (np_all - np_parent) / np_all, 2) if np_all not in (None, 0) and np_parent is not None else None
    res.layers.append(
        AttributionLayer(
            DecompositionLayer.MINORITY_INTEREST,
            "少数股东损益占净利润比",
            mi,
            "%",
            "占比上升说明增量利润更多归属子公司少数股东，归母股东的受益程度被稀释",
            "中性" if mi is None or abs(mi) < 5 else "指向非主营（归母口径被稀释）",
        )
    )

    # 汇总提示
    parts = []
    if rev_g is not None:
        parts.append(f"收入同比 {rev_g:+.2f}%")
    if gm_delta is not None:
        parts.append(f"毛利率 {gm_delta:+.2f}pp")
    if er_delta is not None:
        parts.append(f"期间费用率 {er_delta:+.2f}pp")
    if nr is not None:
        parts.append(f"非经常性损益代理 {nr:+.2f}pp")
    res.verdict_hint = "；".join(parts) if parts else "可用分解层均缺数据"

    return res

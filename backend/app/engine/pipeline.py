"""主链路编排：M4 取证 → M5 判定 → M6 冲突检测 → M7 结论聚合 → M8 反转条件表。

这里承载三个产品判断，都是全题最容易做砸的地方：

1. **冲突不做和稀泥**（M6）。同一件事出现方向相反的证据时，不许写「综合来看影响中性」，
   必须指出冲突性质、按固定优先级裁决、并留下残余不确定性。
2. **结论措辞受约束**（M7）。只用「现有证据支持 / 不支持 / 证据不足」，永不出现买卖建议。
   命题是「合取式」的——「估值回落」且「基本面未恶化」，任一侧被证伪，命题即不成立。
3. **反转条件必须可监控**（M8）。不许写「若基本面变化需重新评估」这种废话，
   每条都要给出监控变量、当前值、触发阈值、**阈值依据**和下次披露时点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from ..analysis.fundamentals import health_check
from ..schemas import (
    Confidence,
    Conflict,
    ConflictPriority,
    Conclusion,
    Evidence,
    FalsificationCondition,
    ThesisType,
    Verdict,
)
from ..templates.gold import Template
from .executors import Ctx


# ==========================================================================
# M6 冲突检测
# ==========================================================================


def detect_conflicts(ctx: Ctx, evidence: list[Evidence]) -> list[Conflict]:
    """找出方向相反或口径打架的证据。返回空列表代表本次没有发现冲突——那是允许的。"""
    conflicts: list[Conflict] = []
    by_id = {e.sub_question_id: e for e in evidence}

    # --- 冲突 1：两种复权口径给出的估值分位结论不一致 ---------------------
    if ctx.series_fwd and ctx.series_none:
        p_fwd = ctx.series_fwd.percentile("pe_reconstructed")
        p_non = ctx.series_none.percentile("pe_reconstructed")
        if p_fwd is not None and p_non is not None:
            e1, e2 = by_id.get("SQ-01"), by_id.get("SQ-02")
            ids = [x.id for x in (e1, e2) if x]
            if abs(p_fwd - p_non) > 25:
                conflicts.append(
                    Conflict(
                        sub_question_id="SQ-01",
                        evidence_ids=ids or ["EV-SQ-01"],
                        nature=(
                            f"同一标的的估值分位在两种复权口径下出现显著分歧："
                            f"前复权序列给出 {p_fwd} 分位，不复权序列给出 {p_non} 分位，"
                            f"相差 {abs(p_fwd - p_non):.1f} 个百分点。"
                            f"根因是除权除息日附近价格跳变与历史每股收益的股本调整口径可能不一致，"
                            f"而本数据源不含股本变动明细，无法完整校正。"
                        ),
                        resolving_priority=ConflictPriority.CALIBER,
                        resolution=(
                            "按优先级顺序，口径一致性优先于其他维度。前复权序列与「用当期 EPS 与"
                            "当期价格同步计算」的官方 pe_ttm 口径更一致（见自校准记录），"
                            "因此以**前复权序列**为准；不复权序列仅作为一致性检验的对照，"
                            "不单独支撑结论。"
                        ),
                        residual_uncertainty=(
                            "该分歧本身说明本产品的估值分位序列存在无法完全消除的口径噪声。"
                            "在除权除息密集的年份附近，分位的绝对水平可能偏移，"
                            "但**趋势方向**（估值在回落还是在抬升）对口径不敏感，仍然可用。"
                        ),
                    )
                )

    # --- 冲突 2：重建值与官方快照偏差超容差 -------------------------------
    if ctx.series_fwd and ctx.series_fwd.calibration:
        gap = ctx.series_fwd.calibration.get("relative_gap")
        e1 = by_id.get("SQ-01")
        if gap is not None and gap > 0.35 and e1:
            conflicts.append(
                Conflict(
                    sub_question_id="SQ-01",
                    evidence_ids=[e1.id, "SNAPSHOT-PE-TTM"],
                    nature=(
                        f"我们重建的最新 PE 与扶摇官方估值快照给出的 pe_ttm 相对偏差达 {gap:.1%}，"
                        f"超出 35% 容差。两者都是真实数据，但算法口径不同："
                        f"官方 pe_ttm 由数据源内部按 TTM 净利润计算，可能与我们的季度合成方式、"
                        f"少数股东损益处理方式存在差异。"
                    ),
                    resolving_priority=ConflictPriority.AUTHORITY,
                    resolution=(
                        "按优先级顺序，在口径无法统一时以**权威来源**为准："
                        "**官方 pe_ttm 的绝对水平更可信**，我们重建的序列只用于提供历史分位这一"
                        "官方接口不提供的信息。UI 上两个数值并列展示，不做合并或平均。"
                    ),
                    residual_uncertainty=(
                        "若两者偏差持续存在，说明我们的重建方法在该标的上系统性偏高或偏低。"
                        "此时分位的**绝对数值**不可作为精确依据，只能读趋势；"
                        "本产品已在 UI 上把该条证据标记为中等置信度并说明原因。"
                    ),
                )
            )

    # --- 冲突 3：估值分位的「低估」读法与周期性提示打架 -------------------
    e_val, e_cyc = by_id.get("SQ-01"), by_id.get("SQ-06")
    if e_val and e_cyc and e_val.verdict is Verdict.SUPPORT:
        sd = e_cyc.value
        if isinstance(sd, (int, float)) and sd > 5.0:
            conflicts.append(
                Conflict(
                    sub_question_id="SQ-01",
                    evidence_ids=[e_val.id, e_cyc.id],
                    nature=(
                        f"两条证据指向相反的解读方向：估值分位证据显示 PE 处于历史 {e_val.display_value}，"
                        f"看起来「便宜」；但周期性检测显示毛利率离散度 {sd:.2f}pp，提示该标的可能存在"
                        f"周期性特征——周期行业的 PE 会在盈利低谷异常走高、盈利高峰异常走低，"
                        f"低 PE 也可能出现在周期顶部。"
                    ),
                    resolving_priority=ConflictPriority.CALIBER,
                    resolution=(
                        "这是**口径适用性冲突**，不是数值冲突。按优先级顺序，口径一致性优先："
                        "PE 分位只有在「盈利不随周期大幅波动」的前提下才能直接读作估值高低。"
                        "本产品无行业景气度数据，无法确认当前所处的周期位置，"
                        "因此**不给出「低估」的方向性判断**，只陈述「PE 处于自身历史 N 分位」这一事实，"
                        "并把周期性限定作为结论的适用边界写入结论与反转条件表。"
                    ),
                    residual_uncertainty=(
                        "无法消除。要消除它必须知道标的当前处于周期的哪个位置，"
                        "这需要行业景气度与产业链数据，超出本数据源能力范围。"
                        "本产品选择把这个不确定性**显式留在结论里**，而不是替用户做一个可能方向错误的判断。"
                    ),
                )
            )

    # --- 冲突 4（背离型）：收入未恶化但利润已恶化 -------------------------
    # 这不是「数据打架」——两个数字都真实且同口径。冲突在于**它们对同一个问题
    # 「基本面有没有恶化」给出了相反回答**。把这种分歧摊开，比强行给一个结论诚实。
    d3, d4 = by_id.get("SQ-03"), by_id.get("SQ-04")
    if d3 and d4 and d3.verdict is Verdict.SUPPORT and d4.verdict is Verdict.REFUTE:
        conflicts.append(
            Conflict(
                sub_question_id="SQ-04",
                evidence_ids=[d3.id, d4.id],
                nature=(
                    f"收入端与利润端的证据方向相反：营业收入同比 {d3.display_value}（未恶化），"
                    f"但归母净利润同比 {d4.display_value}（已恶化）。"
                    f"这是典型的「增收不增利」，两条数据同口径、同报告期，不存在计算错误，"
                    f"因此这是一个**真实的、必须留在结论里的**冲突，不能靠取平均数和稀泥。"
                ),
                resolving_priority=ConflictPriority.CALIBER,
                resolution=(
                    "按题面命题的措辞裁决：「基本面并没有恶化」中的「基本面」，"
                    "在投资语境下最终指向的是**股东的获利能力**，而非经营规模。"
                    "收入增长但归母净利润下滑，意味着增量收入没有转化为股东回报——"
                    "在合并报表层面看是经营扩张，在股东层面看是价值摊薄。"
                    "因此本产品以**归母净利润同比（SQ-04）为判定主证据**，收入端（SQ-03）作为背景证据保留，"
                    "两者在 UI 上并列展示，不折叠成单一数字。"
                ),
                residual_uncertainty=(
                    "无法完全消解。利润下滑的成因决定了这个冲突的严重程度："
                    "若来自一次性因素（计提、诉讼、资产处置），则次年大概率自行修复；"
                    "若来自毛利率结构性下滑或费用刚性扩张，则不会自行修复。"
                    "扶摇的利润表接口不含营业外支出的明细科目，本产品无法区分这两种情形。"
                    "该不确定性已写入反转条件表的首要监控项。"
                ),
            )
        )

    # --- 冲突 5（归因型）：收入支持与利润来源反对并存 ---------------------
    a1, a3 = by_id.get("SQ-01"), by_id.get("SQ-03")
    if a1 and a3 and a1.verdict is Verdict.SUPPORT and a3.verdict is Verdict.REFUTE:
        conflicts.append(
            Conflict(
                sub_question_id="SQ-03",
                evidence_ids=[a1.id, a3.id],
                nature=(
                    "收入端证据支持「主业在扩张」，但利润来源证据显示非经常性损益贡献显著。"
                    "两者并不矛盾——公司确实卖得更多了，但利润的增量有相当部分不是卖出来的。"
                    "这是一种**证据指向不同层次**的冲突，不是数据打架。"
                ),
                resolving_priority=ConflictPriority.CALIBER,
                resolution=(
                    "两条证据**都保留**，不做取舍。命题「盈利改善来自主营业务」问的是"
                    "「增量利润的来源」，而非「收入有没有增长」，"
                    "因此以利润来源证据（SQ-03）为判定的主证据，收入端证据（SQ-01）作为背景。"
                ),
                residual_uncertainty=(
                    "非经常性损益的代理口径（加权 ROE − 扣非加权 ROE）是差值而非直接披露值，"
                    "无法拆分出具体是哪一类非经常项目。若要精确定位，需查阅年报「非经常性损益明细表」。"
                ),
            )
        )

    return conflicts


# ==========================================================================
# M7 结论聚合
# ==========================================================================

_DISCLAIMER = (
    "本结果基于公开数据对投资命题做可验证性检验，展示的是「现有证据支持什么」，"
    "不构成任何投资建议。数据来源：同花顺扶摇（fuyao.aicubes.cn）。"
)


@dataclass
class AggregateInput:
    thesis_type: ThesisType
    raw_text: str
    ticker: str
    name: str


def aggregate(
    ctx: Ctx, evidence: list[Evidence], conflicts: list[Conflict], meta: AggregateInput
) -> Conclusion:
    """把三态证据聚合成结论。命题是合取式的：任一侧被证伪，命题即不成立。"""
    n_sup = sum(1 for e in evidence if e.verdict is Verdict.SUPPORT)
    n_ref = sum(1 for e in evidence if e.verdict is Verdict.REFUTE)
    n_unv = sum(1 for e in evidence if e.verdict is Verdict.UNVERIFIABLE)
    by_id = {e.sub_question_id: e for e in evidence}
    label = f"{meta.name}（{ctx.thscode}）" if meta.name else ctx.thscode

    verdict, statement, coverage = _decide(meta.thesis_type, by_id, label, meta.raw_text)

    fals = falsification_conditions(ctx, evidence, meta.thesis_type)

    limitations = [
        "估值历史分位由本产品用「前复权收盘价 ÷ EPS_TTM」重建得出（扶摇不提供历史估值接口），"
        "属重建口径，与行情终端自带的官方分位可能存在差异。",
        "重建序列与官方 pe_ttm 的相对偏差已在自校准记录中披露；偏差超容差时该条证据降为中等置信度。",
        "扶摇公开接口不含产销量、分产品单价、分部收入、外币敞口，"
        "因此财务分解中的 volume / rate / mix / FX 四层**本产品做不到**，已在拆解结果中显式列为跳过层。",
        "扶摇公开接口不含行业景气度与产业链数据，周期性判断与传导型命题的核心外部变量无法完整验证。",
        "所有金额单位为原币元，未做任何单位换算；缺失值一律保留为空，不补零。",
    ]
    if ctx.run_errors:
        limitations.append(
            f"本次运行有 {len(ctx.run_errors)} 条取数异常已记录在「失败透明」清单中，"
            f"相关子问题已判定为无法验证，未被静默跳过。"
        )

    return Conclusion(
        verdict=verdict,
        statement=statement,
        support_count=n_sup,
        refute_count=n_ref,
        unverifiable_count=n_unv,
        coverage_note=coverage,
        conflicts=conflicts,
        falsification_conditions=fals,
        limitations=limitations,
        disclaimer=_DISCLAIMER,
    )


def _decide(
    ttype: ThesisType,
    by_id: dict[str, Evidence],
    label: str,
    raw_text: str,
) -> tuple[Verdict, str, str]:
    """按命题类型做合取判定。返回 (结论态, 陈述, 覆盖度说明)。"""

    def v(sid: str) -> Optional[Verdict]:
        e = by_id.get(sid)
        return e.verdict if e else None

    if ttype is ThesisType.DIVERGENCE:
        val_side = v("SQ-01")
        price_side = v("SQ-02")
        fund_side = [v(s) for s in ("SQ-03", "SQ-04", "SQ-05")]

        val_ok = val_side is Verdict.SUPPORT
        fund_ref = any(x is Verdict.REFUTE for x in fund_side)
        fund_unknown = all(x is None or x is Verdict.UNVERIFIABLE for x in fund_side)

        if not val_ok and val_side is Verdict.REFUTE:
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。{label} 的估值并未回落——"
                f"重建 PE 当前处于自身历史的中高分位，不满足命题「估值回落」这一前提，"
                f"因此无需再讨论基本面是否恶化。",
                "已完整检验估值侧与基本面侧；由于命题前提不成立，基本面侧证据不作为结论依据。",
            )
        if fund_ref:
            losers = [s for s in ("SQ-03", "SQ-04", "SQ-05") if v(s) is Verdict.REFUTE]
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。虽然估值侧成立，但基本面侧已被证伪："
                f"{'、'.join(losers)} 显示公司经营确有恶化。"
                f"「估值回落但基本面未恶化」的两个条件未能同时满足。",
                "估值侧与基本面侧均已检验；命题被基本面侧单独证伪。",
            )
        if val_ok and not fund_ref and not fund_unknown:
            return (
                Verdict.SUPPORT,
                f"现有证据**支持**该命题。{label} 的估值确已回落（重建 PE 处于自身历史低分位），"
                f"而收入、利润与盈利质量三项均未见恶化。需注意：本结论的适用边界受周期性检测结果约束，"
                f"详见冲突与反转条件。",
                "命题两侧（估值侧 2 条、基本面侧 3 条）均有可用证据支撑。",
            )
        return (
            Verdict.UNVERIFIABLE,
            f"现有证据**不足**以支持或否定该命题。估值侧{'成立' if val_ok else '未获支持'}，"
            f"但基本面侧的 {sum(1 for x in fund_side if x is Verdict.UNVERIFIABLE)} 条证据落入无法验证，"
            f"无法确认基本面是否恶化。按本产品规则，证据不足时不给出方向性结论。",
            "命题未被完整覆盖：关键基本面子问题缺可用数据。",
        )

    if ttype is ThesisType.ATTRIBUTION:
        rev, gm, nr = v("SQ-01"), v("SQ-02"), v("SQ-03")
        minors = v("SQ-06")
        SIDE = {
            "SQ-01": "收入端（营收同比）",
            "SQ-02": "毛利率端",
            "SQ-04": "费用端",
            "SQ-05": "盈利质量（现金含量）",
            "SQ-06": "利润归属（少数股东损益占比）",
        }
        if nr is Verdict.REFUTE:
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。核心判据（非经常性损益代理指标）显示利润中有显著部分"
                f"并非来自可持续的主营经营。收入的增长是真实的，但它不足以支撑「改善来自主营业务」"
                f"这个更强的论断——两者的区别在于增量利润的来源。",
                "五层可做的分解层均已检验；命题被核心判据单独证伪。",
            )
        if nr is Verdict.UNVERIFIABLE or nr is None:
            return (
                Verdict.UNVERIFIABLE,
                f"现有证据**不足**以支持该命题。判定「利润来源」的核心判据"
                f"（加权 ROE 与扣非加权 ROE 之差）在本标的该报告期不可得，"
                f"因此无法区分利润改善是来自主营还是非经常项目。",
                "核心判据缺失，命题的关键一问未被回答。",
            )

        # 核心判据通过后，再看支撑侧。这里必须**按实际三态生成措辞**：
        # 早先的版本写死了「收入端与毛利率端未能提供支持」，当收入端其实是
        # 支持态时，结论陈述会与证据卡自相矛盾（宁德时代实测踩到）。
        dissent = [SIDE[s] for s in ("SQ-01", "SQ-02") if v(s) is Verdict.REFUTE]
        blocked = [SIDE[s] for s in ("SQ-01", "SQ-02") if v(s) is None or v(s) is Verdict.UNVERIFIABLE]
        weak_quality = minors is Verdict.REFUTE

        if rev is Verdict.SUPPORT and gm is Verdict.SUPPORT and not weak_quality:
            return (
                Verdict.SUPPORT,
                f"现有证据**支持**该命题。收入同比增长、毛利率同步改善、"
                f"且非经常性损益对利润的贡献不显著，三方面共同指向「盈利改善主要来自主营业务」。"
                f"仍需注意本产品无法完成量价拆分，收入增长的内部结构未经验证。",
                "五层可做的分解层均已检验，命题各侧均获支持。",
            )
        if rev is Verdict.SUPPORT and gm is Verdict.UNVERIFIABLE:
            return (
                Verdict.SUPPORT,
                f"现有证据**支持**该命题，但强度有限。非经常性损益不显著、收入同比为正，"
                f"两条核心证据同向；毛利率端的变动幅度落在中性区间，既未支持也未反对。"
                f"因此结论建立在两条证据而非三条之上。",
                f"核心判据与收入端支持；{'、'.join(blocked)}未提供方向性证据，已在覆盖度中声明。",
            )
        if dissent:
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。虽然非经常性损益对利润的贡献不显著"
                f"（排除了「靠一次性收益」这一最直接的反例），但 {'、'.join(dissent)} 已转向恶化"
                f"——利润改善缺乏主营业务扩张的规模证据，「来自主营业务」这一论断因此不成立。",
                f"五层可做的分解层均已检验；{'、'.join(dissent)} 反对，命题被支撑侧证伪。",
            )
        return (
            Verdict.UNVERIFIABLE,
            f"现有证据**不足**以支持或否定该命题。核心判据未发现非经常性损益的显著贡献，"
            f"但支撑侧证据不可得，无法确认主营业务本身是否在扩张。",
            f"核心判据通过，但 {'、'.join(blocked) or '支撑侧子问题'} 缺可用数据，命题未被完整覆盖。",
        )

    # 传导型
    n_unv = sum(
        1 for s in ("SQ-01", "SQ-04", "SQ-05") if v(s) is Verdict.UNVERIFIABLE
    )
    return (
        Verdict.UNVERIFIABLE,
        f"现有证据**不足**以支持或否定该命题。本类命题的核心前提——"
        f"标的在产业链中的位置（主营构成）与其所依赖的外部变量——在本产品接入的数据源中"
        f"**不存在**，因此传导链条的两端都无法观测。"
        f"这不是「暂时取数失败」，而是能力边界："
        f"本产品诚实地把这个命题标记为不可验证，并已指出需要什么数据、从哪里可以获得。"
        f"把一个不可验证的命题标为「已验证」，比标为「无法验证」危险得多。",
        f"{n_unv} 个关键子问题因数据源能力边界落入无法验证；"
        f"成本端与成本转嫁能力两条子问题提供了间接证据，但不足以支撑传导结论。",
    )


# ==========================================================================
# M8 反转条件表
# ==========================================================================


def _next_disclosure(today: Optional[date] = None) -> str:
    """下一个 A 股定期报告披露截止日。"""
    t = today or datetime.now(timezone.utc).astimezone().date()
    schedule = [
        (date(t.year, 4, 30), "当年一季报（法定截止 4/30）"),
        (date(t.year, 8, 31), "当年半年报（法定截止 8/31）"),
        (date(t.year, 10, 31), "当年三季报（法定截止 10/31）"),
        (date(t.year + 1, 4, 30), "当年年报 + 次年一季报（法定截止 4/30）"),
    ]
    for d, label in schedule:
        if d > t:
            return f"{d.isoformat()}（{label}）"
    return "年内已无定期报告节点"


def falsification_conditions(
    ctx: Ctx, evidence: list[Evidence], ttype: ThesisType
) -> list[FalsificationCondition]:
    """产出可监控的反转条件。每条都必须有阈值依据，禁止拍脑袋。

    **当前值一律从证据对象里读，不另走一条计算路径。**
    早先这里是拿年报重算一遍的，结果证据卡用的是最新季报（2026Q2）、
    反转条件表用的是去年年报（2025FY），同一个指标在同一个页面出现两个数字。
    从证据读值让「一致性」成为结构保证，而不是靠两处代码记得同步改。
    """
    conds: list[FalsificationCondition] = []
    by_id = {e.sub_question_id: e for e in evidence}
    nxt = _next_disclosure()

    def pick(*sq_ids: str) -> tuple[Optional[float], str, str]:
        """按顺序找到第一个给出数值的证据。返回 (值, 报告期描述, 来源证据 id)。"""
        for sid in sq_ids:
            e = by_id.get(sid)
            if e is None or e.value is None:
                continue
            try:
                v = float(e.value)
            except (TypeError, ValueError):
                continue
            return v, e.provenance.report_period, sid
        return None, "", ""

    if ttype in (ThesisType.DIVERGENCE, ThesisType.ATTRIBUTION):
        rev, rev_p, _ = pick("SQ-03", "SQ-01")
        if rev is not None:
            conds.append(
                FalsificationCondition(
                    monitored_variable="营业收入同比增速",
                    current_value=f"{rev:+.2f}%（{rev_p}）",
                    trigger_threshold="由正转负（< 0%）",
                    direction="下行突破",
                    flips_sub_question="SQ-03 收入端未恶化 → 恶化了",
                    marginal_impact="high",
                    next_disclosure=nxt,
                    threshold_basis=(
                        "0% 是「增长 / 萎缩」的会计分界，不是经验取值。"
                        "收入同比转负意味着主营规模开始收缩，命题「基本面未恶化」的前提直接消失。"
                    ),
                )
            )
        prof, prof_p, _ = pick("SQ-04")
        if prof is not None:
            conds.append(
                FalsificationCondition(
                    monitored_variable="归母净利润同比增速",
                    current_value=f"{prof:+.2f}%（{prof_p}）",
                    trigger_threshold="由正转负（< 0%）",
                    direction="下行突破",
                    flips_sub_question="SQ-04 利润端未恶化 → 恶化了",
                    marginal_impact="high",
                    next_disclosure=nxt,
                    threshold_basis="同上，0% 是盈亏增长的分界。",
                )
            )
        # 现金含量：三态判据是「≥0.8 支持 / 0.5–0.8 中性 / <0.5 反对」，
        # 因此触发阈值取 0.8（跌出支持区间）而非 0.5——当前已在 0.75 时，
        # 用 0.5 会显示成「还没触发」，但结论其实早已不在支持下。
        cash, cash_p, _ = pick("SQ-05")
        if cash is not None:
            already_weak = cash < 0.8
            conds.append(
                FalsificationCondition(
                    monitored_variable="净利润现金含量（经营现金流净额 ÷ 归母净利润）",
                    current_value=f"{cash:.2f} 倍（{cash_p}）",
                    trigger_threshold=(
                        "回到 0.8 倍以上（当前已跌破，正处中性区间）"
                        if already_weak else "跌破 0.8 倍（退出支持区间）"
                    ),
                    direction="下行突破" if not already_weak else "上行修复",
                    flips_sub_question="SQ-05 盈利质量未恶化 → 恶化了",
                    marginal_impact="medium",
                    next_disclosure=nxt,
                    threshold_basis=(
                        "0.8 倍取自本产品 SQ-05 判据的支持区间下沿，不是经验值："
                        "该条只有 ≥0.8 才算「盈利质量未恶化」，0.5–0.8 之间不构成方向性证据，"
                        "低于 0.5 则转为明确反对。因此真正能翻转结论的阈值是 0.8，不是 0.5。"
                    ),
                )
            )

    if ttype is ThesisType.ATTRIBUTION:
        nr, nr_p, _ = pick("SQ-03")
        conds.append(
            FalsificationCondition(
                monitored_variable="非经常性损益影响（加权ROE − 扣非加权ROE）",
                current_value=f"{nr:+.2f}pp（{nr_p}）" if nr is not None else "本期不可得",
                trigger_threshold="超过 2pp",
                direction="上行突破",
                flips_sub_question="SQ-03 利润来源 → 从主营转为非主营",
                marginal_impact="high",
                next_disclosure=nxt,
                threshold_basis=(
                    "2pp 取自本产品 SQ-03 的判定阈值，含义是「非经常性损益对 ROE 的贡献超过 2 个百分点」，"
                    "此时它在利润中的权重已不容忽视。该阈值在模板中事先写定，不随个案调整。"
                ),
            )
        )

    if ttype is ThesisType.DIVERGENCE:
        sd = by_id.get("SQ-06")
        conds.append(
            FalsificationCondition(
                monitored_variable="毛利率多期离散度（周期性代理指标）",
                current_value=(
                    f"{sd.display_value}（{sd.provenance.report_period}）"
                    if sd and sd.value is not None else "样本不足"
                ),
                trigger_threshold="标准差超过 5pp",
                direction="上行突破",
                flips_sub_question="SQ-06 周期性判定 → 从「可常规解读」转为「须附加周期限定」",
                marginal_impact="medium",
                next_disclosure=nxt,
                threshold_basis=(
                    "5pp 为本产品在模板中预设的周期性判定阈值。它一旦被触发，"
                    "SQ-01 的 PE 分位就不能再被读作「便宜」，结论从「估值回落」"
                    "退化为「估值处于历史低位，但周期位置未知」。"
                ),
            )
        )

    if ttype is ThesisType.TRANSMISSION:
        conds.append(
            FalsificationCondition(
                monitored_variable="主营业务构成（分部收入占比）",
                current_value="不可得 —— 本数据源不提供分部收入",
                trigger_threshold="获得任何一期分部收入数据",
                direction="信息可得性变化",
                flips_sub_question="SQ-01 产业链位置未知 → 已知",
                marginal_impact="high",
                next_disclosure="不适用（取决于是否接入新的数据源）",
                threshold_basis=(
                    "本条不是数值阈值，而是**信息可得性阈值**。"
                    "传导型命题当前的全部结论都卡在「不知道公司靠什么赚钱」这一步；"
                    "一旦拿到分部收入，整条链路的可验证性会立即改变。"
                    "明确写出「什么信息能改变结论」是题目对本表的要求，"
                    "而信息可得性的改变本身就是一种改变结论的方式。"
                ),
            )
        )

    return conds

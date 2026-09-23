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
from typing import Callable, Optional

from ..analysis.fundamentals import health_check
from ..schemas import (
    Charts,
    Confidence,
    Conflict,
    ConflictPriority,
    Conclusion,
    Evidence,
    FalsificationCondition,
    MarginChart,
    ProfitChart,
    SeriesPoint,
    ThesisType,
    ValuationChart,
    Verdict,
    WithheldChart,
)
from ..templates.gold import Template
from .executors import Ctx, _prev_same_period


# ==========================================================================
# M6 冲突检测
# ==========================================================================


def detect_conflicts(
    ctx: Ctx, evidence: list[Evidence], ttype: ThesisType
) -> list[Conflict]:
    """找出方向相反或口径打架的证据。返回空列表代表本次没有发现冲突——那是允许的。

    **必须传入命题类型。** 子问题 ID（SQ-01、SQ-03……）只在同一套模板内唯一，
    跨模板完全同名不同义：SQ-03 在背离型是「营业收入」，在归因型是「非经常性损益」。
    早先这里按 ID 硬编码取证据，于是归因型的冲突正文会写成
    「估值分位证据显示 PE 处于历史 +54.80%……毛利率离散度 5.97pp」——
    引用的两条证据都不是它说的东西。类型是这条链路的第一参数，不能靠调用方自觉。
    """
    conflicts: list[Conflict] = []
    by_id = {e.sub_question_id: e for e in evidence}

    # --- 冲突 1：两种复权口径给出的估值分位结论不一致 ---------------------
    # 仅背离型：只有该模板把 SQ-01 定义为估值分位。
    if ttype is ThesisType.DIVERGENCE and ctx.series_fwd and ctx.series_none:
        p_fwd = ctx.series_fwd.percentile("pe_reconstructed")
        p_non = ctx.series_none.percentile("pe_reconstructed")
        if p_fwd is not None and p_non is not None:
            e1 = by_id.get("SQ-01")
            # 这条冲突的两端是**两条序列**（前复权 / 不复权），不是两张卡：
            # 不复权那条只是同一取数路径下的对照序列，从来没有独立的证据卡。
            # 早先的写法是 `ids or ["EV-SQ-01"]` —— 一个凭空编出来的 id，
            # 用来把列表凑到 schema 要求的长度（凑不够 2 会直接抛校验异常）。
            # 界面会把它当证据编号印出来，读者拿着一串不存在的编号去核对。
            # 现在如实声明对照的那一端是序列，不编 id。e1 不存在时不出这条冲突：
            # 一条指不出任何一张卡的冲突，读者无从核对。
            if abs(p_fwd - p_non) > 25 and e1 is not None:
                conflicts.append(
                    Conflict(
                        sub_question_id="SQ-01",
                        evidence_ids=[e1.id],
                        counterparty=(
                            "不复权口径重建的 PE 序列（与前复权序列同源取数、同一重建方法，"
                            "仅复权方式不同；本产品不把它作为独立证据卡）"
                        ),
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
    if ttype is ThesisType.DIVERGENCE and ctx.series_fwd and ctx.series_fwd.calibration:
        gap = ctx.series_fwd.calibration.get("relative_gap")
        e1 = by_id.get("SQ-01")
        if gap is not None and gap > 0.35 and e1:
            our_pe = ctx.series_fwd.calibration.get("our_latest_pe")
            off_pe = ctx.series_fwd.calibration.get("official_pe_ttm")
            conflicts.append(
                Conflict(
                    sub_question_id="SQ-01",
                    # 另一端此前写成 `"SNAPSHOT-PE-TTM"` —— 它不是任何一张证据卡的 id，
                    # 而是官方快照这个**数值**，被编出来凑 schema 的长度下界。
                    # 前端把 evidence_ids 逐字渲染成「涉及证据 …」，读者会去卡列表里找它，找不到。
                    evidence_ids=[e1.id],
                    counterparty=(
                        f"扶摇官方估值快照的 pe_ttm = {off_pe}"
                        f"（随估值快照接口一同取回，作为本条重建序列的对拍基准；"
                        f"它不是本产品的子问题，故无独立证据卡）"
                    ),
                    nature=(
                        f"我们重建的最新 PE 为 {our_pe}，扶摇官方估值快照给出的 pe_ttm 为 {off_pe}，"
                        f"相对偏差达 {gap:.1%}，超出 35% 容差。"
                        f"两者都是真实数据，但算法口径不同："
                        f"官方 pe_ttm 由数据源内部按 TTM 净利润计算，"
                        f"可能与我们的季度合成方式、少数股东损益处理方式存在差异。"
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
    # 仅背离型：SQ-01 是估值分位、SQ-06 是毛利率离散度，只有这套模板同时具备这两条。
    e_val, e_cyc = by_id.get("SQ-01"), by_id.get("SQ-06")
    if ttype is ThesisType.DIVERGENCE and e_val and e_cyc and e_val.verdict is Verdict.SUPPORT:
        sd = e_cyc.value
        if isinstance(sd, (int, float)) and sd > 5.0:
            conflicts.append(
                Conflict(
                    sub_question_id="SQ-01",
                    evidence_ids=[e_val.id, e_cyc.id],
                    nature=(
                        f"两条证据指向相反的解读方向：估值分位证据显示 PE 处于"
                        f"自有可重建区间的 {e_val.display_value}，"
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
    if (
        ttype is ThesisType.DIVERGENCE
        and d3 and d4
        and d3.verdict is Verdict.SUPPORT and d4.verdict is Verdict.REFUTE
    ):
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
    if (
        ttype is ThesisType.ATTRIBUTION
        and a1 and a3
        and a1.verdict is Verdict.SUPPORT and a3.verdict is Verdict.REFUTE
    ):
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
    extra_limitations: list[str] = field(default_factory=list)
    """澄清回答带来的额外适用边界。默认空 —— 没有回答时聚合结果与引入该功能前逐字相同。"""


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
    # 澄清回答带来的边界声明。放在最后：它解释的是「以上这些证据该怎么读」，
    # 不是又一条取数口径说明。
    limitations.extend(meta.extra_limitations)

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
        # 子问题可能因澄清回答被移出本轮范围（例如用户把命题收窄为「仅收入与利润」，
        # SQ-05 被 drop）。下面每句结论文案都按**实际在场的**子问题来写，
        # 而不是假设三条基本面子问题一定都跑过 —— 否则文案会替一条没跑的证据说话。
        FUND_IDS = ("SQ-03", "SQ-04", "SQ-05")
        FUND_NAME = {"SQ-03": "收入", "SQ-04": "利润", "SQ-05": "盈利质量"}
        present_fund = [s for s in FUND_IDS if by_id.get(s) is not None]

        val_side = v("SQ-01")
        price_side = v("SQ-02")
        fund_side = [v(s) for s in FUND_IDS]

        val_ok = val_side is Verdict.SUPPORT
        fund_ref = any(x is Verdict.REFUTE for x in fund_side)
        fund_unknown = all(x is None or x is Verdict.UNVERIFIABLE for x in fund_side)

        # 估值分位的**窗口**必须跟着分位一起出现。
        # 重建序列的长度由可用报告期数决定，实测 361 / 371 个交易日（约 1.4 年），
        # 而「处于自身历史低分位」读起来是「上市以来的历史低分位」——
        # 两句话对读者的含义差得很远，前者最多说明「比最近一年多便宜」。
        # 这里从证据卡的报告期原样取出窗口，不另算、不另写。
        e_val = by_id.get("SQ-01")
        val_win = ""
        if e_val is not None and e_val.value is not None:
            val_win = (
                f"（分位窗口 {e_val.provenance.report_period}，"
                f"由可用报告期数决定，**不是「自上市以来」**）"
            )

        if not val_ok and val_side is Verdict.REFUTE:
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。{label} 的估值并未回落——"
                f"重建 PE 当前处于**自有可重建区间**的中高分位{val_win}，"
                f"不满足命题「估值回落」这一前提，"
                f"因此无需再讨论基本面是否恶化。",
                "已完整检验估值侧与基本面侧；由于命题前提不成立，基本面侧证据不作为结论依据。",
            )
        if fund_ref:
            losers = [s for s in FUND_IDS if v(s) is Verdict.REFUTE]
            val_clause = (
                "虽然估值侧成立，但"
                if val_ok
                else "估值侧未获支持（该侧子问题本轮无法验证），但"
            )
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。{val_clause}基本面侧已被证伪："
                f"{'、'.join(losers)} 显示公司经营确有恶化。"
                f"「估值回落但基本面未恶化」的两个条件未能同时满足。",
                (
                    "估值侧与基本面侧均已检验；命题被基本面侧单独证伪。"
                    if val_ok
                    else "估值侧落入无法验证，但基本面侧自身已足以证伪命题，"
                         "结论不以估值侧为转移。"
                ),
            )
        if not val_ok and not fund_ref and not fund_unknown:
            # 估值侧本身取不到数 —— 结论卡在这里，而不是卡在基本面。
            # 若沿用下面那条「N 条基本面证据落入无法验证」的文案，N 会是 0，读出来是句废话。
            #
            # 结论正文里**不粘贴证据卡上的整段原因**。此前这里把
            # `unverifiable.what_is_needed` 直接拼进正文，而那一段是写给工程看的
            # （「重建同业各家的 PE 序列需要逐只调历史K线与利润表……」），
            # 拼进来既不像结论、又常常以「，」收尾，正文里就出现了「，。」。
            # 结论只说「哪一侧没验成」并指出原因在哪张卡上，具体原因由卡自己讲。
            e1 = by_id.get("SQ-01")
            tail = (
                "为什么取不到数、需要补哪一步，写在 SQ-01 那张证据卡上。"
                if (e1 is not None and e1.unverifiable is not None)
                else "本轮未取到该子问题所需的数。"
            )
            return (
                Verdict.UNVERIFIABLE,
                f"现有证据**不足**以支持或否定该命题。基本面侧未见恶化，"
                f"但命题的前半句「估值已回落」本轮无法验证 —— {tail}"
                f"按本产品规则，命题的一端取不到数时不给出方向性结论："
                f"把无法验证的那一侧当作成立，等于替用户的假设背书。",
                # 「基本面侧证据不作为结论依据」是错的：上面那句「基本面侧未见恶化」
                # 恰恰用到了基本面侧的证据。此前那句话否定了同一句结论自己的依据。
                "命题未被完整覆盖：估值侧落入无法验证，因此不给出方向性结论。",
            )
        if val_ok and not fund_ref and not fund_unknown:
            # 三条基本面俱在时，逐字沿用**改动前那一句**（「收入、利润与盈利质量三项均未见恶化」），
            # 连连接词都用原来的「与」。这一句是默认路径（用户一句都没回答）上真正渲染出来的正文，
            # 为兼容「SQ-05 被澄清移出」而把它顺手改成顿号连接，就足以让
            # 「未回答时的行为逐字不变」这条不变量在例题上失效 —— 修一处不该动的措辞，
            # 换不到任何东西，却让交付文档里的承诺变成假的。
            if len(present_fund) == 3:
                fund_clause = "收入、利润与盈利质量三项均未见恶化"
            else:
                fund_clause = (
                    "、".join(FUND_NAME[s] for s in present_fund) + "各项均未见恶化"
                )
            n_val = sum(1 for s in ("SQ-01", "SQ-02") if by_id.get(s) is not None)
            # 「周期性」这一句必须按 SQ-06 的**实际状态**写。
            #
            # 早先在这里写死了一句「本结论的适用边界受周期性检测结果约束，详见冲突与反转条件」，
            # 而中国平安那一条的 SQ-06 因有效报告期不足 4 期（保险公司的利润表里没有
            # operating_costs 这个口径）而算不出离散度，falsification_conditions 对无数值的
            # 子问题**不生成行**——于是反转条件表里根本没有周期性这一行，读者被指向一个不存在的小节。
            # 这与「承诺一个界面上没有的控件」是同一类错：句子描述的是它没有做的事。
            # 更要紧的是方向反了：句子让读者以为周期性已被排雷，实际是**根本没检测**。
            cyc = by_id.get("SQ-06")
            if cyc is None:
                # 澄清把这条移出了本轮范围（如命题收窄），就不该再提它。
                cyc_clause = ""
            elif not isinstance(cyc.value, (int, float)):
                cyc_clause = (
                    "此外须明说：本轮**未能完成周期性检测**（毛利率多期序列不足 4 期，"
                    "反转条件表中因此没有对应的监控行），本结论对周期行业的 PE 陷阱未设防，"
                    "估值分位只能按「未排雷」来读。"
                )
            elif cyc.value > 5.0:
                cyc_clause = (
                    "需注意：周期性检测提示该标的**存在周期特征**，估值分位不可机械读作「低估」，"
                    "限制条件与翻转阈值见「冲突与反转条件」。"
                )
            else:
                cyc_clause = "周期性检测未提示显著周期特征，估值分位可作常规解读。"
            return (
                Verdict.SUPPORT,
                f"现有证据**支持**该命题。{label} 的估值确已回落"
                f"（重建 PE 处于**自有可重建区间**的低分位{val_win}），"
                f"而{fund_clause}。{cyc_clause}",
                f"命题两侧（估值侧 {n_val} 条、基本面侧 {len(present_fund)} 条）均有可用证据支撑。",
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
            # 尾巴上这句讲的是「规模证据在不在场」，而规模证据就是收入端（SQ-01）本身。
            # 这里踩过**两次**同一个坑：先把 dissent 列表写死（改成了按三态生成），
            # 但这句话里的结论仍然写死 —— 宁德时代收入端是支持态（+54.80%），
            # 于是同一页上一边摆着 +54.80% 的支持卡，一边说「缺乏规模证据」。
            # 改一半等于没改：读者看到的是整句话，不是被改对的那半句。
            if rev is Verdict.REFUTE:
                tail = "主营业务并未扩张，利润改善缺乏规模支撑"
            elif rev is Verdict.SUPPORT:
                tail = "收入端虽有扩张，但盈利能力同步下降，增长未体现为主营业务质量的改善"
            else:
                tail = "收入端是否在扩张无法确认，规模证据本身不可得"
            return (
                Verdict.REFUTE,
                f"现有证据**不支持**该命题。虽然非经常性损益对利润的贡献不显著"
                f"（排除了「靠一次性收益」这一最直接的反例），但 {'、'.join(dissent)} 已转向恶化"
                f"——{tail}，「来自主营业务」这一论断因此不成立。",
                f"五层可做的分解层均已检验；{'、'.join(dissent)} 反对，命题被支撑侧证伪。",
            )
        # 兜底分支。能落到这里的状态**不止一种**，措辞必须按实际状态生成。
        # 早先写死了「支撑侧证据不可得」+「缺可用数据」，但还有一种落法：
        # 收入端与毛利率端**都是支持态**，挡住结论的是利润归属（SQ-06 少数股东损益占比反对）。
        # 那时这两句话描述的事情一件也没发生——支撑侧完全可得，缺的也不是数据。
        if blocked:
            head = (
                f"核心判据未发现非经常性损益的显著贡献，但 {'、'.join(blocked)} 缺可用数据，"
            )
            why = "无法确认主营业务本身是否在扩张"
            cov = f"核心判据通过，但 {'、'.join(blocked)} 缺可用数据，命题未被完整覆盖。"
        else:
            head = (
                f"核心判据未发现非经常性损益的显著贡献，收入端与毛利率端均为支持态，"
                f"但利润归属一项显示增量利润有显著部分并未归属上市公司股东。"
            )
            why = (
                "本产品不据此否定命题——这一条反对的不是「改善来自主营」，"
                "而是「改善归属于上市公司股东」，两者是不同的论断；"
                "但在归属口径被稀释的前提下，也不给出一条支持结论"
            )
            cov = (
                f"核心判据与支撑侧均通过，但 {SIDE['SQ-06']} 反对，"
                f"命题在「归属上市公司股东」这一口径上未被完整覆盖，故整体不判为支持。"
            )
        return (
            Verdict.UNVERIFIABLE,
            f"现有证据**不足**以支持或否定该命题。{head}{why}。",
            cov,
        )

    # 传导型
    n_unv = sum(
        1 for s in ("SQ-01", "SQ-04", "SQ-05") if v(s) is Verdict.UNVERIFIABLE
    )
    # 覆盖度这句必须按 SQ-02 / SQ-03 的**实际三态**写。早先写死了一句
    # 「成本端与成本转嫁能力两条子问题提供了间接证据，但不足以支撑传导结论」，
    # 而宁德时代那一例实测下来：SQ-02 是**反对**态（营业成本率累计变动 −2.01pp，
    # 方向与「上游涨价」相反），SQ-03 则因窗口内成本率并未上升而根本无法判定。
    # 同一句话于是错了两处：把一条明确反对的证据说成「间接证据」，
    # 又把一条**没跑出结论**的证据说成「提供了证据」。
    # 这正是本仓库反复记录的那一类错——证据卡上的状态换了，结论的措辞没跟着换。
    cost_side, pass_side = v("SQ-02"), v("SQ-03")
    if cost_side is Verdict.SUPPORT:
        cost_clause = "成本端确有显著变化，间接支持传导链条的前半段"
    elif cost_side is Verdict.REFUTE:
        cost_clause = "成本端在观察窗口内并未发生显著变化，传导链条的前半段因此不成立"
    else:
        cost_clause = "成本端序列不足以计算，链条前半段无从观测"
    if pass_side is Verdict.SUPPORT:
        pass_clause = "成本转嫁能力一项显示公司具备把成本压力转给下游的定价权"
    elif pass_side is Verdict.REFUTE:
        pass_clause = "成本转嫁能力一项显示公司不具备向下游转嫁成本的能力"
    else:
        pass_clause = "成本转嫁能力不可判定（窗口内成本率未上升，命题预设的前提没有出现）"
    return (
        Verdict.UNVERIFIABLE,
        f"现有证据**不足**以支持或否定该命题。本类命题的核心前提——"
        f"标的在产业链中的位置（主营构成）与其所依赖的外部变量——在本产品接入的数据源中"
        f"**不存在**，因此传导链条的两端都无法观测。"
        f"这不是「暂时取数失败」，而是能力边界："
        f"本产品诚实地把这个命题标记为不可验证，并已指出需要什么数据、从哪里可以获得。"
        f"把一个不可验证的命题标为「已验证」，比标为「无法验证」危险得多。",
        f"{n_unv} 个关键子问题因数据源能力边界落入无法验证；"
        f"另两条属于链条中段的信息——{cost_clause}，{pass_clause}。"
        f"但这两条既不足以为传导结论提供支持，也不足以否定它："
        f"链条两端的「标的位置」与「外部变量」本身不可观测，中段信息无从挂靠。",
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


def _invalidated_by_clarification(evidence: list[Evidence], sq_id: str) -> bool:
    """该子问题的判定是不是**被澄清环节整体作废**的（而不是取数得来的）。

    判据是证据卡的溯源终点：澄清环节改判的卡片，endpoint 写的是
    "(clarification: ...)"，它没有对应的取数调用；取数路径生成的卡片不会这样写。

    为什么不用「value is None」来判：样本不足、取数失败、异常退出也都会是 None，
    但那些情形下序列本身就不存在或不完整，图要么没有、要么本来就画不全；
    这里要抓的是**序列好端端地在 ctx 里，而使用它的那条证据已经被撤了**——
    图会继续把它画出来并印上一个不该再出现的数值。
    """
    for e in evidence:
        if e.sub_question_id == sq_id:
            return e.provenance.endpoint.startswith("(clarification")
    return False


def build_charts(ctx: Ctx, evidence: Optional[list[Evidence]] = None) -> Optional[Charts]:
    """把本次已经取到的数据整理成三张图。

    **不发起任何新的取数**——图上每一个点都能在证据卡里找到出处。
    图只是同一批证据的另一种呈现方式，不是新的论据来源。

    `evidence` 传入后，本函数会检查每张图所依据的子问题是否已被作废；
    被作废的图撤下来并写进 `withheld`，而不是照旧画出来 ——
    细节见 `_invalidated_by_clarification` 与 `Charts.withheld`。
    """
    evidence = evidence or []
    withheld: list[WithheldChart] = []
    val = None
    s = ctx.series_fwd
    if s is not None:
        pts = s.metrics.get("pe_reconstructed") or []
        if pts:
            # 序列可能上千个交易日，逐点下发既大又没必要。按固定间隔抽稀，
            # 但**首尾两点必留**——横轴的两端被截掉会让趋势看着变形。
            step = max(1, len(pts) // 220)
            kept = pts[::step]
            if kept[-1] is not pts[-1]:
                kept = kept + [pts[-1]]

            def _bin(name: str) -> Optional[float]:
                idx = s.percentile(name)
                return idx

            vals = [p.value for p in kept if p.value is not None]
            bands: dict[str, float] = {}
            allv = sorted(p.value for p in pts if p.value is not None)
            if len(allv) >= 20:
                for q, key in ((0.10, "p10"), (0.25, "p25"), (0.50, "p50"),
                               (0.75, "p75"), (0.90, "p90")):
                    bands[key] = round(allv[min(len(allv) - 1, int(len(allv) * q))], 2)

            calib = s.calibration or {}
            val = ValuationChart(
                series=[
                    SeriesPoint(label=_day(p.date_ms), value=round(p.value, 3))
                    for p in kept
                    if p.value is not None
                ],
                latest=round(vals[-1], 3) if vals else None,
                percentile=s.percentile("pe_reconstructed"),
                bands=bands,
                official_pe_ttm=calib.get("official_pe_ttm"),
                relative_gap=calib.get("relative_gap"),
                caliber="前复权收盘价 ÷ EPS_TTM（本产品重建）",
                note=(
                    "扶摇公开接口不提供历史估值序列，本图由本产品重建。"
                    "重建值与官方 pe_ttm 的偏差见图中标注；偏差在容差内时趋势可用，"
                    "绝对水平只作参考。"
                ),
            )

    # 估值图绑定的是 SQ-01。若 SQ-01 已被澄清环节作废，这张图就**没有出处的证据卡**了。
    # 撤下来，并把原因写明白——静默省略与照旧展示都不行：
    # 静默省略会让「用户选了口径 → 图消失」看起来像 bug；照旧展示则更糟，
    # 屏幕上会留着一个用户刚刚拒绝过的口径算出来的分位，而且它还带着「当前 PE x，y 分位」
    # 这种读起来就是答案的页脚。
    if val is not None and _invalidated_by_clarification(evidence, "SQ-01"):
        withheld.append(
            WithheldChart(
                name="估值分位带图（重建 PE 序列）",
                backs_sub_question="SQ-01",
                reason=(
                    "SQ-01（估值分位）在本轮被澄清环节改判为无法验证 —— 用户把参照系指定成了"
                    "另一种口径，而本产品没有那条取数路径。参照系一换，这张按「自有可重建区间」"
                    "重建的 PE 序列就不再对应用户要问的那个问题；把它继续画出来，"
                    "等于把用户刚刚拒绝的口径的数字塞回屏幕上。"
                    "本图撤下而不是加注「仅供参考」：图上印着一个分位，它就会被当成答案。"
                ),
            )
        )
        val = None

    periods: list[str] = []
    rev: list[Optional[float]] = []
    prof: list[Optional[float]] = []
    rows = sorted(
        [r for r in (ctx.income_q or []) if r.get("period_end_ms")],
        key=lambda r: r["period_end_ms"],
    )
    for r in rows:
        prev = _prev_same_period(ctx.income_q, r)
        if prev is None:
            continue
        pv = prev.get("operating_income")
        pf = prev.get("parent_holder_net_profit")
        cv = r.get("operating_income")
        cf = r.get("parent_holder_net_profit")
        if not pv or pv == 0:
            continue
        periods.append(f"{r.get('fiscal_year')}{r.get('fiscal_period')}")
        rev.append(round(100.0 * (cv - pv) / abs(pv), 2) if cv is not None else None)
        prof.append(round(100.0 * (cf - pf) / abs(pf), 2) if cf is not None and pf else None)
    profit = (
        ProfitChart(periods=periods, revenue_yoy=rev, profit_yoy=prof)
        if len(periods) >= 2
        else None
    )

    gm = getattr(ctx, "gm_series", None) or []
    cr = getattr(ctx, "cost_ratio_series", None) or []
    margin = None
    if len(gm) >= 2 and len(gm) == len(cr):
        # 横轴刻度必须用真实报告期，不能写 T-1/T-2 这种占位——
        # 没有报告期的数字读者无法与财报核对，图上就成了一条无出处的曲线。
        all_labels = [f"{r.get('fiscal_year')}{r.get('fiscal_period')}" for r in rows]
        if len(all_labels) >= len(gm):
            labels = all_labels[-len(gm):]
        elif any(not str(x).startswith("T-") for x in all_labels):
            labels = [f"第 {i + 1} 期" for i in range(len(gm))]
        else:
            labels = all_labels
        margin = MarginChart(
            periods=labels,
            gross_margin=[round(x, 2) for x in gm],
            cost_ratio=[round(x, 2) for x in cr],
        )

    if val is None and profit is None and margin is None and not withheld:
        return None
    return Charts(valuation=val, profit=profit, margin=margin, withheld=withheld)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def falsification_conditions(
    ctx: Ctx, evidence: list[Evidence], ttype: ThesisType
) -> list[FalsificationCondition]:
    """产出可监控的反转条件。每条都必须有阈值依据，禁止拍脑袋。

    **当前值一律取自证据卡的 display_value，不另起一条格式化路径。**
    两处早先各自算、各自格式化同一个数字，临界值上会差最后一位
    （证据卡 1.85 倍、反转条件表 1.84 倍），读者无从判断哪个对。
    更严重的是本表早先按子问题 ID 硬编码，而 ID 只在同一套模板内唯一：
    归因型下 SQ-03 是「非经常性损益」、SQ-04 是「期间费用率」，
    于是「营业收入同比增速」一栏被填进了 ROE 差额、真正的营收增速在表里根本不出现，
    标题与数字对不上。现在按命题类型分别取 ID，数字直接引用证据卡的展示串——
    一致性成为结构保证，而不是靠两处代码记得同步改。
    """
    conds: list[FalsificationCondition] = []
    by_id = {e.sub_question_id: e for e in evidence}
    nxt = _next_disclosure()

    def add(
        sid: str,
        *,
        variable: str,
        threshold: str,
        direction: str,
        flips: str,
        impact: str,
        basis: str,
        latched_when: "Callable[[float], bool] | None" = None,
    ) -> None:
        """从证据卡取当前值。证据卡缺失或无数值时**不生成该行**——宁可少一行，不可编一个数。

        `latched_when` 判断**当前值是否已经满足触发阈值**。这张表的名字叫「反转条件」，
        读者默认它讲的是「未来可能发生的事」；但实测里确实存在已经越线的行：
        贵州茅台的归母净利润同比已经是 −1.95%（阈值是「由正转负」），
        宁德时代传导型那行的成本率变动已经是 −2.01pp（阈值是「回到 3pp 以内」）。
        这两行此前被原样展示成待触发的风险，读者据此会认为结论还很稳——方向正好反了。
        传 None 表示该行的阈值不是「当前值已经越线」这种形态（例如现金含量那两行）。
        """
        e = by_id.get(sid)
        if e is None or e.value is None:
            return
        latched = False
        if latched_when is not None:
            try:
                latched = bool(latched_when(float(e.value)))
            except (TypeError, ValueError):
                latched = False
        conds.append(
            FalsificationCondition(
                monitored_variable=variable,
                current_value=f"{e.display_value}（{e.provenance.report_period}）",
                trigger_threshold=threshold,
                direction=f"已触发 · 原本监测「{direction}」" if latched else direction,
                flips_sub_question=flips,
                marginal_impact=impact,
                next_disclosure=nxt,
                threshold_basis=basis,
                already_triggered=latched,
            )
        )

    # 现金含量：三态判据是「≥0.8 支持 / 0.5–0.8 中性 / <0.5 反对」，
    # 因此触发阈值取 0.8（跌出支持区间）而非 0.5——当前已在 0.75 时，
    # 用 0.5 会显示成「还没触发」，但结论其实早已不在支持下。
    def cash_row(sid: str, flips: str) -> None:
        e = by_id.get(sid)
        if e is None or e.value is None:
            return
        try:
            weak = float(e.value) < 0.8
        except (TypeError, ValueError):
            return
        add(
            sid,
            variable="净利润现金含量（经营现金流净额 ÷ 归母净利润）",
            threshold=(
                "回到 0.8 倍以上（当前已跌破，正处中性区间）"
                if weak else "跌破 0.8 倍（退出支持区间）"
            ),
            direction="上行修复" if weak else "下行突破",
            flips=flips,
            impact="medium",
            basis=(
                "0.8 倍取自本产品 SQ-05 判据的支持区间下沿，不是经验值："
                "该条只有 ≥0.8 才算「盈利质量未恶化」，0.5–0.8 之间不构成方向性证据，"
                "低于 0.5 则转为明确反对。因此真正能翻转结论的阈值是 0.8，不是 0.5。"
            ),
        )

    if ttype is ThesisType.DIVERGENCE:
        add(
            "SQ-03",
            variable="营业收入同比增速",
            threshold="由正转负（< 0%）",
            direction="下行突破",
            flips="SQ-03 收入端未恶化 → 恶化了",
            impact="high",
            basis=(
                "0% 是「增长 / 萎缩」的会计分界，不是经验取值。"
                "收入同比转负意味着主营规模开始收缩，命题「基本面未恶化」的前提直接消失。"
            ),
            latched_when=lambda x: x < 0,
        )
        add(
            "SQ-04",
            variable="归母净利润同比增速",
            threshold="由正转负（< 0%）",
            direction="下行突破",
            flips="SQ-04 利润端未恶化 → 恶化了",
            impact="high",
            basis="同上，0% 是盈亏增长的分界。",
            latched_when=lambda x: x < 0,
        )
        cash_row("SQ-05", "SQ-05 盈利质量未恶化 → 恶化了")
        add(
            "SQ-06",
            variable="毛利率多期离散度（周期性代理指标）",
            threshold="标准差超过 5pp",
            direction="上行突破",
            flips="SQ-06 周期性判定 → 从「可常规解读」转为「须附加周期限定」",
            impact="medium",
            basis=(
                "5pp 为本产品在模板中预设的周期性判定阈值。它一旦被触发，"
                "SQ-01 的 PE 分位就不能再被读作「便宜」，结论从「估值回落」"
                "退化为「估值处于历史低位，但周期位置未知」。"
            ),
            latched_when=lambda x: x > 5.0,
        )

    elif ttype is ThesisType.ATTRIBUTION:
        # 归因型的主判据是「利润来源」，不是「收入增速」——
        # 收入增长只排除了「收入没增长」这一个反例，说明不了利润从哪来。
        # 因此本表把非经常性损益放在第一行，其余各条按对结论的实际影响力排序。
        add(
            "SQ-03",
            variable="非经常性损益影响（加权ROE − 扣非加权ROE）",
            threshold="超过 2pp",
            direction="上行突破",
            flips="SQ-03 利润来源 → 从主营转为非主营（核心判据翻转，总结论随之翻转）",
            impact="high",
            basis=(
                "2pp 取自本产品 SQ-03 的判定阈值，含义是「非经常性损益对 ROE 的贡献超过 2 个百分点」，"
                "此时它在利润中的权重已不容忽视。该阈值在模板中事先写定，不随个案调整。"
            ),
            latched_when=lambda x: x > 2.0,
        )
        add(
            "SQ-01",
            variable="营业收入同比增速",
            threshold="由正转负（< 0%）",
            direction="下行突破",
            flips="SQ-01 主业规模在扩张 → 不再扩张",
            impact="high",
            basis=(
                "0% 是「增长 / 萎缩」的会计分界。归因型命题的完整表述是「盈利改善**来自主营业务**」，"
                "收入转负意味着产生利润的主业本身在收缩，「来自主营业务」的规模基础不复存在。"
            ),
            latched_when=lambda x: x < 0,
        )
        add(
            "SQ-02",
            variable="毛利率同比变动",
            threshold="跌破 −1pp（跌出中性区间，由「不构成证据」转为「反对」）",
            direction="下行突破",
            flips="SQ-02 主业盈利能力 → 由改善或中性转为恶化",
            impact="medium",
            basis=(
                "−1pp 取自本产品 SQ-02 判据的反对区间边界。取 −1pp 而非 0，是因为 ±1pp 内"
                "属于产品结构与季度节奏造成的正常波动，本产品的判据在该区间内不做方向性判断；"
                "越过 −1pp 才构成「主业盈利能力恶化」的证据。"
            ),
            latched_when=lambda x: x < -1.0,
        )
        # SQ-04 的触发条件是两个条件的**合取**，不能只看费用率降幅。
        # 判据原文是「费用率下降 > 1pp 且收入增速 ≤ 0 → 反对；费用率下降但收入同步增长 → 支持（规模效应）」。
        # 早先这一行只写了「降幅超过 1pp」，把一个合取条件写成了单条件——
        # 宁德时代实测费用率降幅 −2.35pp（早已越过 1pp），但同期收入同比 +54.80%，
        # 该条的实际判定是**支持**。按单条件算，这一行会被标成「已触发」，
        # 而它描述的翻转根本没有发生，读者反而会以为结论已被削弱。
        rev_e = by_id.get("SQ-01")
        rev_val = rev_e.value if (rev_e is not None and isinstance(rev_e.value, (int, float))) else None
        add(
            "SQ-04",
            variable="期间费用率同比变动",
            threshold="降幅超过 1pp **且同期收入未增长**（由中性或支持转为「省出来的」）",
            direction="下行突破",
            flips="SQ-04 中性/支持 → 反对（利润改善被归因于费用压缩而非主业变强）",
            impact="medium",
            basis=(
                "1pp 取自本产品 SQ-04 判据的分界；「收入未增长」（≤0%）是与它并列的第二个条件。"
                "两个条件必须同时成立才判为「省出来的」——费用率降幅大而收入同步增长属于规模效应，"
                "那是主业变强的证据而不是相反。降本增效与主业变强是两回事，"
                "这个阈值组合就是用来把两者分开的。"
            ),
            latched_when=lambda x: x < -1.0 and rev_val is not None and rev_val <= 0,
        )
        cash_row("SQ-05", "SQ-05 利润的现金支撑 → 恶化了")

    elif ttype is ThesisType.TRANSMISSION:
        add(
            "SQ-02",
            variable="营业成本率累计变动（上游成本压力的报表可见部分）",
            threshold="绝对变动回到 3pp 以内（成本端压力消失）",
            direction="收敛至中性",
            flips="SQ-02 成本端确有变化 → 无变化（命题的传导前提不成立）",
            impact="high",
            basis=(
                "3pp 取自本产品 SQ-02 判据的阈值。它一旦收敛回 3pp 以内，"
                "「外部成本变化」这个命题前提本身就不成立了——"
                "没有成本变化，就谈不上成本在产业链上如何分配。"
            ),
            latched_when=lambda x: abs(x) <= 3.0,
        )
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

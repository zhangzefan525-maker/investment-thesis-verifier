"""手工精写的命题拆解模板（gold templates）。

## 为什么要手工写模板，而不是让 LLM 自由拆

LLM 自由拆解有两个致命问题：
1. **会拆出产品取不到数据的子问题**，然后为了「完成」而编造——直接违反「不虚构」约束。
2. **判定规则会写成「显著改善」「明显低估」这类无法证伪的词**——命题验证就退化成了写研报。

所以这里把「哪些子问题、用哪个指标、什么阈值、期望什么方向」全部手工写死。
LLM 的职责被压缩到：把用户那句话解析成 `{类型, 标的, 时间窗}`，然后**实例化**模板里的标的与时间窗。
模板本身不含任何数字，只有规则。

每个子问题都带 `executor` 键，指向 engine/executors.py 里的一个具体取数+判定函数。
没有 executor 的子问题不允许出现在模板里——这是「拆得出就要证得了」的硬约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.collect import HISTORY_LIMIT
from ..schemas import DecompositionLayer, SubQuestion, ThesisType, Verdict

# 多期序列的观察窗长度，取自取数层那一个常量——**不在这里另写一个数**。
#
# 教训：这三处窗口早先都写作「最近 8 个报告期」，而取数层固定取 12 期，
# 于是同一个「累计变动」在拆解面板（按 8 期声明）与证据卡（按 12 期计算）里
# 得到两个口径。窗口长度是取数层的属性，模板只能是它的转述；
# 写成两个字面量，「改一处忘一处」就只是时间问题。
_WINDOW = f"最近 {HISTORY_LIMIT} 期单季报告期（实际可用期数取决于披露，见证据卡报告期一栏）"


@dataclass
class Template:
    thesis_type: ThesisType
    label: str
    description: str
    sub_questions: list[SubQuestion]
    layer_coverage: list[DecompositionLayer] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _sq(
    sid: str,
    text: str,
    metric: str,
    source: str,
    window: str,
    rule: str,
    threshold: str,
    expected: Verdict,
    rationale: str,
    layer: DecompositionLayer | None = None,
) -> SubQuestion:
    return SubQuestion(
        id=sid,
        text=text,
        metric=metric,
        data_source=source,
        time_window=window,
        decision_rule=rule,
        threshold=threshold,
        expected_direction=expected,
        layer=layer,
        rationale=rationale,
    )


# ==========================================================================
# 一、背离型：「估值回落但基本面未恶化」
# ==========================================================================

DIVERGENCE = Template(
    thesis_type=ThesisType.DIVERGENCE,
    label="背离型 · 估值与基本面背离",
    description=(
        "命题断言「价格/估值下行了，但公司经营没有变差」。验证它需要两侧同时成立："
        "一侧证明估值确实回落，另一侧证明基本面确实未恶化。任何一侧不成立，命题即被证伪。"
    ),
    layer_coverage=[
        DecompositionLayer.REVENUE_GROWTH,
        DecompositionLayer.GROSS_MARGIN,
        DecompositionLayer.NON_RECURRING,
        DecompositionLayer.CASH_QUALITY,
    ],
    notes=[
        "「回落」是相对概念，必须有参照系。本模板的参照系是标的的**自身历史分布**，"
        f"而非某个绝对数值。参照系的实际窗口长度由可用的报告期数决定：本产品取最近 {HISTORY_LIMIT} 期"
        "财报重建 EPS_TTM 阶梯，因此重建 PE 序列约覆盖 1–2 年，**不是「自上市以来」**。"
        "这句话早先写作「最多回溯 10 年」，与实测窗口（361 / 371 个交易日）不符——"
        "把能力写得比实际更强，是本产品最不该犯的一类错。窗口长度逐条显示在证据卡的报告期一栏。",
        "周期性行业的 PE 会在盈利低谷异常走高、盈利高点异常走低，本模板用一条独立子问题"
        "（SQ-06）专门检测这个陷阱，避免机械解读。",
    ],
    sub_questions=[
        _sq(
            "SQ-01",
            "标的当前估值处于自有可重建区间的什么位置？是否确实已回落？",
            "重建 PE 序列的当前分位",
            "扶摇 历史K线（前复权）+ 合并利润表 → 本地重建 PE 序列；与扶摇估值快照 pe_ttm 对拍校准",
            f"自可重建序列的起始日至最新交易日；窗口长度取决于可用报告期数（当前取最近 {HISTORY_LIMIT} 期财报）",
            "计算重建 PE 当前值在自有可重建区间内的分位；分位越低代表估值越靠近该区间的低位。"
            "必须同时写出该区间的起止日期——区间长度由可用报告期数决定，通常只有 1–2 年，"
            "**不是「自上市以来」**。同时必须报告重建值与官方 pe_ttm 的相对偏差；"
            "偏差超容差则本条结论降级为低置信度",
            "当前 PE 分位 ≤ 30 → 估值确已回落；30–70 → 中性；≥ 70 → 估值并未回落",
            Verdict.SUPPORT,
            "这是命题的前半句。「回落」若本身不成立，整个命题不成立，"
            "后续再多基本面证据也没有意义",
        ),
        _sq(
            "SQ-02",
            "标的价格自身是否确实回落？幅度多大？",
            "前复权收盘价的区间回撤幅度",
            "扶摇 /api/a-share/prices/historical（adjust=forward）",
            "近 3 年最高收盘价至最新收盘价",
            "计算区间最大回撤 = (最新收盘价 − 区间最高收盘价) / 区间最高收盘价，"
            "并给出从最高点至今的历时天数",
            "回撤 ≤ −20% → 价格显著回落；−20%~0% → 温和；> 0 → 未回落",
            Verdict.SUPPORT,
            "与 SQ-01 互为交叉验证。估值回落可能来自价格下跌，也可能来自盈利上升；"
            "本条单独锁定价格侧，两条一起看才能区分这两种完全不同的情形",
        ),
        _sq(
            "SQ-03",
            "公司收入端是否恶化？",
            "营业收入同比增速",
            "扶摇 /api/a-share/financials/income-statements（annual + quarterly）",
            "最新报告期 vs 上一年同一 fiscal_period（口径一致优先）",
            "计算营业收入同比增速。使用同 fiscal_period 对比以保证口径一致，"
            "不用季度对年报这类跨口径比较",
            "同比 ≥ 0 → 收入未恶化；< 0 → 收入已恶化（命题该侧被证伪）",
            Verdict.SUPPORT,
            "「基本面未恶化」的第一层直接证据。收入是基本面的起点，"
            "收入下滑时即使利润还撑着，也只是时间问题",
            DecompositionLayer.REVENUE_GROWTH,
        ),
        _sq(
            "SQ-04",
            "公司利润端是否恶化？",
            "归母净利润同比增速",
            "扶摇 /api/a-share/financials/income-statements",
            "最新报告期 vs 上一年同一 fiscal_period",
            "计算归母净利润同比增速。注意与营收增速对照：收入增长而利润下滑说明成本端失控",
            "同比 ≥ 0 → 利润未恶化；< 0 → 利润已恶化",
            Verdict.SUPPORT,
            "利润是估值分位的分母，也是「基本面」最直接的度量。"
            "与 SQ-03 的差异方向本身就是重要信息",
        ),
        _sq(
            "SQ-05",
            "盈利质量有没有暗中恶化？利润是否真的转化成了现金？",
            "净利润现金含量 = 经营活动现金流净额 ÷ 归母净利润",
            "扶摇 /api/a-share/financials/cash-flow-statements + income-statements",
            "最新报告期",
            "计算比值。长期显著低于 1 说明账面利润没有变成现金，"
            "可能对应应收账款堆积或收入确认激进",
            "≥ 0.8 → 现金含量健康；0.5–0.8 → 需关注；< 0.5 → 盈利质量存疑，"
            "此时即使营收利润为正，「未恶化」的判断也要打折",
            Verdict.SUPPORT,
            "只看到营收利润为正就宣布「基本面未恶化」是常见错误。"
            "现金流是防这类误判的最后一道闸",
            DecompositionLayer.CASH_QUALITY,
        ),
        _sq(
            "SQ-06",
            "标的所处行业是否具有周期性？如果有，PE 分位该怎么读？",
            "所属行业 + 毛利率波动幅度（周期性代理指标）",
            "扶摇 /api/meta/tickers/search + /api/a-share/financials/indicators（多期毛利率）",
            _WINDOW,
            "周期行业的 PE 会在盈利低谷异常走高、盈利高峰异常走低。"
            "本产品无行业景气度数据，因此改用毛利率的历史波动幅度作为周期性代理："
            f"计算最近可得的 {HISTORY_LIMIT} 期毛利率的标准差，波动越大越可能具周期性",
            "毛利率标准差 > 5pp → 判定为强周期性，SQ-01 的 PE 分位解读必须附加周期限定，"
            "不得机械读作「低估」；≤ 5pp → 周期性弱，PE 分位可常规解读",
            Verdict.UNVERIFIABLE,
            "这是本产品最重要的口径陷阱防护。周期股在盈利高点 PE 很低，"
            "看起来「便宜」实际上可能是顶；在低谷 PE 很高，看起来「贵」实际上可能是底。"
            "没有这一条，SQ-01 会给出方向性错误的结论",
        ),
    ],
)


# ==========================================================================
# 二、归因型：「盈利改善来自主营业务」
# ==========================================================================

ATTRIBUTION = Template(
    thesis_type=ThesisType.ATTRIBUTION,
    label="归因型 · 盈利改善的来源",
    description=(
        "命题断言「利润变好是因为主业做得好」。验证它的关键是把利润变化拆开，"
        "把「主营驱动的部分」和「非主营驱动的部分」分开，看增量利润主要来自哪一侧。"
    ),
    layer_coverage=[
        DecompositionLayer.REVENUE_GROWTH,
        DecompositionLayer.GROSS_MARGIN,
        DecompositionLayer.EXPENSE_RATIO,
        DecompositionLayer.NON_RECURRING,
        DecompositionLayer.MINORITY_INTEREST,
    ],
    notes=[
        "本模板显式声明做不到的分解层：产销量（volume）、分产品单价（rate）、"
        "分部收入（mix）、外币敞口（FX）——扶摇公开接口均不提供。"
        "不去用近似数据糊弄，列入 skipped_layers 并在 UI 展示。",
    ],
    sub_questions=[
        _sq(
            "SQ-01",
            "主营业务的规模是否在扩张？",
            "营业收入同比增速",
            "扶摇 /api/a-share/financials/income-statements",
            "最新报告期 vs 上一年同一 fiscal_period",
            "计算营收同比。收入正增长意味着利润改善至少有主营规模支撑",
            "同比 > 0 → 支持；= 0 → 中性；< 0 → 反对（利润改善不可能来自萎缩的主业）",
            Verdict.SUPPORT,
            "主营改善的必要条件。收入没增长而利润增长，增量必然来自成本、费用或非经常项目",
            DecompositionLayer.REVENUE_GROWTH,
        ),
        _sq(
            "SQ-02",
            "主营业务的盈利能力是否在改善？",
            "毛利率同比变动（pp）",
            "扶摇 /api/a-share/financials/income-statements（operating_income / operating_costs）",
            "最新报告期 vs 上一年同一 fiscal_period",
            "计算毛利率及其同比变动。毛利率提升说明产品定价能力或成本结构在改善，属主营质量改善",
            "同比变动 > +1pp → 支持；−1pp ~ +1pp → 中性；< −1pp → 反对",
            Verdict.SUPPORT,
            "收入增长可能是靠降价换来的。毛利率是区分「增收又增利」与「增收不增利」的分水岭",
            DecompositionLayer.GROSS_MARGIN,
        ),
        _sq(
            "SQ-03",
            "利润改善中有多少来自非经常性损益？",
            "加权ROE − 扣非加权ROE（pp）",
            "扶摇 /api/a-share/financials/indicators（index_weighted_avg_roe / index_deduct_weighted_avg_roe）",
            "最新报告期",
            "扣非 ROE 剔除了非经常性损益，两者之差即为非经常性损益对 ROE 的贡献。"
            "这是本数据源下对「非经常性损益」最直接的可用代理",
            "差额 > 2pp → 反对（利润里非主营成分显著）；≤ 2pp → 支持",
            Verdict.REFUTE,
            "这是命题的核心判据。若利润增量主要来自资产处置、政府补助、公允价值变动等，"
            "则「改善来自主营业务」被直接证伪",
            DecompositionLayer.NON_RECURRING,
        ),
        _sq(
            "SQ-04",
            "利润改善是不是只是省出来的？",
            "期间费用率变动（pp）",
            "扶摇 /api/a-share/financials/income-statements（sales_fee / manage_fee / R&D / operating_income）",
            "最新报告期 vs 上一年同一 fiscal_period",
            "计算（销售费用+管理费用+研发费用）÷ 营业收入，及其同比变动",
            "费用率下降 > 1pp 且收入增速 ≤ 0 → 反对（是省出来的不是赚出来的）；"
            "费用率下降但收入同步增长 → 支持（规模效应）；费用率上升 → 中性偏反对",
            Verdict.UNVERIFIABLE,
            "「降本增效」常被当作主业改善来叙事，但它和「主业变强」是两回事。"
            "本条把两者分开，并单独给一个证据态",
            DecompositionLayer.EXPENSE_RATIO,
        ),
        _sq(
            "SQ-05",
            "利润的现金含量如何？改善是否体现在现金上？",
            "净利润现金含量（倍）",
            "扶摇 /api/a-share/financials/cash-flow-statements + income-statements",
            "最新报告期",
            "经营活动现金流净额 ÷ 归母净利润",
            "≥ 0.8 → 支持；0.5–0.8 → 中性；< 0.5 → 反对",
            Verdict.SUPPORT,
            "真正主营改善会带来现金流入。利润增长但现金流恶化，"
            "通常指向应收账款激增或收入确认激进",
            DecompositionLayer.CASH_QUALITY,
        ),
        _sq(
            "SQ-06",
            "增量利润有多少真正归属上市公司股东？",
            "少数股东损益占净利润比例",
            "扶摇 /api/a-share/financials/income-statements（net_profit / parent_holder_net_profit）",
            "最新报告期",
            "计算 (净利润 − 归母净利润) ÷ 净利润",
            "占比 < 5% → 支持（利润基本归属母公司股东）；≥ 5% → 反对（归母口径被显著稀释）",
            Verdict.SUPPORT,
            "看合并报表的净利润增长就下结论是常见错误——增量可能主要归子公司少数股东",
            DecompositionLayer.MINORITY_INTEREST,
        ),
        _sq(
            "SQ-07",
            "收入增长中有多少来自销量、多少来自价格？",
            "收入 = 销量 × 单价 的二维拆分",
            "需要产销量明细与分产品单价数据（扶摇公开接口不提供）",
            "最新报告期 vs 上年同期",
            "本层无法计算。扶摇公开接口不提供产销量与分产品单价，"
            "因此 volume 与 rate 两层无法分离",
            "不可判定 —— 本条永久标记为无法验证，直到接入提供产销量明细的数据源",
            Verdict.UNVERIFIABLE,
            "这是收入分解的下一层。做不到就要明说做不到，"
            "而不是用「量价齐升」这类没有数据支撑的措辞带过",
            DecompositionLayer.VOLUME,
        ),
    ],
)


# ==========================================================================
# 三、传导型：「某项外部变化将影响产业链利润分配」
# ==========================================================================

TRANSMISSION = Template(
    thesis_type=ThesisType.TRANSMISSION,
    label="传导型 · 外部变化沿产业链传导",
    description=(
        "命题断言「上游/下游的某个变化会传导到本标的的利润」。"
        "验证它需要三样东西：本标的在产业链中的位置、外部变量的可观测数据、传导的定量证据。"
        "本产品只有这三者中的一半——因此这一类命题是「无法验证」这个证据态的主场。"
    ),
    notes=[
        "诚实地讲：扶摇公开接口覆盖的是单个上市主体的行情与财务，"
        "不覆盖产业链上下游的价格、供需、产能数据。"
        "因此传导型命题在本数据源下**大部分子问题会落入无法验证**。",
        "这不是产品缺陷，而是产品的核心主张：**告诉用户这条命题在现有数据条件下不可验证，"
        "并精确指出缺什么、从哪能拿到**——比编一段似是而非的产业分析有价值得多。",
    ],
    layer_coverage=[DecompositionLayer.GROSS_MARGIN, DecompositionLayer.EXPENSE_RATIO],
    sub_questions=[
        _sq(
            "SQ-01",
            "本标的的收入与利润来自哪些业务？各占多少？",
            "主营业务构成（分部收入占比）",
            "需要分部收入 / 主营构成数据（扶摇公开接口不提供）",
            "最新年报",
            "无法计算。没有主营构成数据，就无法确定本标的在产业链中的实际位置，"
            "也就无法判断某个外部变化会通过哪条路径影响它",
            "不可判定 —— 这是传导型命题一切后续推理的前提",
            Verdict.UNVERIFIABLE,
            "不知道公司靠什么赚钱，就无从谈起「上游涨价会影响它」。"
            "这一条不成立时，后面所有传导推理都建立在猜测上",
            DecompositionLayer.MIX,
        ),
        _sq(
            "SQ-02",
            "本标的的成本端近期是否发生了变化？",
            "营业成本率（营业成本 ÷ 营业收入）的多期序列",
            "扶摇 /api/a-share/financials/income-statements（operating_costs / operating_income）",
            _WINDOW,
            "计算营业成本率序列并观察其变化幅度与方向。成本率大幅上升是上游涨价的间接信号——"
            "但注意这是**间接**证据，无法区分是原材料涨价还是产品结构变化",
            "成本率变动绝对值 > 3pp → 成本端确有显著变化，可支撑传导链条的前半段；"
            "≤ 3pp → 成本端稳定，传导链条前半段不成立",
            Verdict.SUPPORT,
            "成本率是唯一能从公开报表里间接观测到的「上游影响」，"
            "但它混杂了结构变化，因此本条只给中低置信度",
            DecompositionLayer.GROSS_MARGIN,
        ),
        _sq(
            "SQ-03",
            "本标的是否具备把成本变化转嫁给下游的能力？",
            "毛利率与营业成本率的同向性",
            "扶摇 /api/a-share/financials/income-statements（多期）",
            _WINDOW,
            "若成本率上升而毛利率基本稳定或上升，说明公司有能力提价转嫁成本，"
            "具备定价权；若成本率上升同时毛利率下降，说明成本压力由公司自行承担",
            "成本率上升且毛利率同向下降 > 1pp → 无转嫁能力；"
            "成本率上升但毛利率稳定/上升 → 有转嫁能力",
            Verdict.SUPPORT,
            "「利润分配」这个词的含义就是谁能把成本转嫁给谁。"
            "毛利率与成本率的对应关系，是定价权最直接的报表证据",
            DecompositionLayer.GROSS_MARGIN,
        ),
        _sq(
            "SQ-04",
            "同业公司是否呈现同样的成本变化？",
            "同行业可比公司的营业成本率同期变动",
            "需要同行业成分股清单与批量财务数据（扶摇公开接口不提供按行业批量取财务的路径）",
            "最新报告期",
            "无法计算。判断一个成本变化是行业性的还是个体的，必须找同业对照，"
            "但本数据源没有按行业批量取财务指标的接口",
            "不可判定 —— 缺同业对照组，无法排除个股特异性",
            Verdict.UNVERIFIABLE,
            "独家好或独家坏，含义完全不同。没有同业对照，"
            "就无法判断这个变化是不是整个行业都在经历",
        ),
        _sq(
            "SQ-05",
            "上游/下游的价格或供需数据在本数据源中可得吗？",
            "产业链外部变量（如原材料价格、下游需求指标）",
            "扶摇公开接口覆盖范围：A股行情、财务报表与指标、估值、指数板块、基金、期货期权、"
            "集合竞价、特色数据；**不含**宏观数据、产业链价格、行业供需",
            "命题涉及的时间窗",
            "本数据源明确不提供产业链与宏观数据。诚实结论：该类变量无法从本产品当前接入的"
            "数据源取得，命题的核心外部变量不可观测",
            "不可判定 —— 记为无法验证，并给出可行替代路径",
            Verdict.UNVERIFIABLE,
            "这是本类命题的根节点。外部变量本身取不到时，"
            "任何「传导」结论都只能是叙事而非验证",
        ),
    ],
)


TEMPLATES: dict[ThesisType, Template] = {
    ThesisType.DIVERGENCE: DIVERGENCE,
    ThesisType.ATTRIBUTION: ATTRIBUTION,
    ThesisType.TRANSMISSION: TRANSMISSION,
}

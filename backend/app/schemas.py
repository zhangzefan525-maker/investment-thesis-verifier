"""核心数据契约。

设计原则：把产品约束写进 schema，而不是写在 README 里靠自觉。
任何一个「证据对象」在类型层面就不可能缺字段——缺了就构造不出来。

约束来源：
- longbridge-fundamentals: 数据实时获取 / 来源可溯 / 报告期以数据为准 /
  拆分维度明示 / 结论必须有据 / 失败必须透明 / 维度跳过须显式声明 / 标的代码必须标注
- equity-investment-thesis: 区分「事实 / 逻辑 / 预期 / 风险」/ 允许中性结论 / 少形容词多驱动因素
- financial-statements: 差异分解分类（volume / rate / mix / one-time / timing / FX）
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------


class Verdict(str, Enum):
    """证据三态。题目原文要求「支持、反对与无法验证」三类并列。"""

    SUPPORT = "support"
    REFUTE = "refute"
    UNVERIFIABLE = "unverifiable"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ThesisType(str, Enum):
    """命题类型学。三类命题对数据的要求差异极大，产品必须显式区分。"""

    DIVERGENCE = "divergence"      # 背离型：估值回落但基本面未恶化
    ATTRIBUTION = "attribution"    # 归因型：盈利改善来自主营业务
    TRANSMISSION = "transmission"  # 传导型：外部变化影响产业链利润分配


class UnverifiableCategory(str, Enum):
    """无法验证的原因分类。不允许只写一句「数据缺失」。"""

    DATA_NOT_EXIST = "data_not_exist"          # 该类数据在可用数据源中不存在
    NOT_DISCLOSED = "not_disclosed"            # 数据存在但公司未披露到该颗粒度
    CALIBER_MISMATCH = "caliber_mismatch"      # 数据存在但口径不可比
    PERIOD_NOT_DUE = "period_not_due"          # 报告期尚未到来
    SOURCE_UNREACHABLE = "source_unreachable"  # 数据源调用失败（必须透明，不可静默跳过）
    PERMISSION_DENIED = "permission_denied"    # 数据源有该能力但当前凭据无权
    INCONCLUSIVE_RANGE = "inconclusive_range"  # 数据取到了，但落在判定规则的中性区间，不足以定论


class DecompositionLayer(str, Enum):
    """财务差异分解层。

    取自 Anthropic financial-statements skill 的 volume/rate/mix/one-time/timing/FX 分类。
    我们只声明「能做到哪一层」，做不到的层必须显式列入 skipped_layers。
    """

    REVENUE_GROWTH = "revenue_growth"            # 收入增速（volume × rate 的合成，无法再拆）
    GROSS_MARGIN = "gross_margin"                # 毛利率变动
    EXPENSE_RATIO = "expense_ratio"              # 期间费用率变动
    NON_RECURRING = "non_recurring"              # 非经常性损益（用扣非 ROE 与 ROE 之差做代理）
    MINORITY_INTEREST = "minority_interest"      # 少数股东损益占比
    CASH_QUALITY = "cash_quality"                # 利润的现金含量
    # 以下为「声明做不到」的层
    VOLUME = "volume"                            # 量：需产销量明细，扶摇不提供
    RATE = "rate"                                # 价：需分产品单价，扶摇不提供
    MIX = "mix"                                  # 结构：需分部收入，扶摇不提供
    FX = "fx"                                    # 汇率：需外币敞口，扶摇不提供


class ConflictPriority(str, Enum):
    """证据冲突裁决优先级。固定顺序，不允许临场拍脑袋。"""

    CALIBER = "caliber_consistency"        # 1 口径一致性
    FRESHNESS = "data_freshness"           # 2 数据新鲜度
    AUTHORITY = "source_authority"         # 3 来源权威性
    SAMPLE_SIZE = "sample_size"            # 4 样本量


# --------------------------------------------------------------------------
# 溯源基元：任何进入结论的数字都必须挂得上这个
# --------------------------------------------------------------------------


class Provenance(BaseModel):
    """数据溯源。每个数字的来源、报告期、口径、抓取时间。"""

    source: str = Field(description="数据来源机构，如「同花顺扶摇」")
    endpoint: str = Field(description="原始接口路径，如 GET /api/a-share/valuations/snapshot")
    request_params: dict[str, Any] = Field(default_factory=dict, description="请求参数")
    report_period: str = Field(description="报告期或数据时点，如 2025-4 或 2026-09-23")
    caliber: str = Field(description="口径说明，如「TTM」/「前复权」/「重建口径」")
    unit: str = Field(description="单位，如 CNY / % / 倍")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = Field(default=None, description="上游 request_id，用于追溯")
    raw: dict[str, Any] = Field(default_factory=dict, description="原始返回片段，不可加工")

    @field_validator("report_period", "caliber", "unit", "source", "endpoint")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("溯源字段不可为空——每个数字都必须能回到原始返回")
        return v


class UnverifiableDetail(BaseModel):
    """无法验证时必须回答的三问。缺一条就构造不出来。"""

    category: UnverifiableCategory
    what_is_needed: str = Field(description="需要什么信息才能变成可验证")
    where_to_get: str = Field(description="该信息何时、从哪可获得")
    failure_evidence: str = Field(
        description="失败证据：原始错误码 / 空返回 / 字段缺失的具体描述。禁止静默跳过"
    )

    @field_validator("what_is_needed", "where_to_get", "failure_evidence")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # 与 Provenance._not_blank 同一条规矩。裸 str 允许空串，
        # 于是「需要什么 / 何时何地可得 / 失败证据」三问可以填三个空串照样构造成功——
        # 「有字段」和「答了」是两回事，只有内容非空才算答了。
        if not v or not v.strip():
            raise ValueError("无法验证三问不可留空——留空等同于没答，请写明具体内容")
        if v.strip() in {"-", "—", "N/A", "待定", "无", "暂无"}:
            raise ValueError(f"三问取值 '{v}' 等同于未答——请写明具体内容，不要用占位符")
        return v


# --------------------------------------------------------------------------
# M1/M2 命题
# --------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    """澄清问题。原命题通常是模糊的，必须先问清才能验证。

    这是 v1 → v2 修订的输入端：用户答了，v2 的前提就是用户的原话；
    没答，v2 的前提才是本产品的默认假设。两者必须能分辨，所以 `answer`
    与 `assumption` 分开存，而不是把回答也塞进 assumption 里。
    """

    question: str
    why_it_matters: str = Field(description="这个问题不清楚会导致哪条子问题无法判定")
    options: list[str] = Field(default_factory=list)
    assumption: str = Field(description="用户未回答时本产品采用的默认假设——必须写明而不是暗含")
    answer: Optional[str] = Field(
        default=None, description="用户在澄清环节给出的回答。为 None 表示未回答，此时走 assumption"
    )
    impact: str = Field(
        default="",
        description="该回答对本轮验证产生的**实际**影响。回答了就必须说明它改了什么；"
        "若它不改变任何取数与判定，也要如实写「不改变下游」——不允许用模糊措辞把无效果说成有效果",
    )


class ThesisVersion(BaseModel):
    version: Literal["v1", "v2"]
    text: str = Field(description="命题陈述")
    horizon: str = Field(description="时间窗，如「未来 2 个季度」")
    decision_context: str = Field(default="", description="这条命题要用来支持什么决策")


class ThesisDiff(BaseModel):
    """v1 → v2 的修订差异。题目要求「澄清并修订投资命题」，必须看得见改了什么。"""

    field: str
    before: str
    after: str
    reason: str


class ParsedThesis(BaseModel):
    raw_text: str
    thesis_type: ThesisType
    type_rationale: str = Field(description="为什么判为这一类——依据关键词与可验证性结构")
    ticker: Optional[str] = Field(default=None, description="六位代码")
    thscode: Optional[str] = Field(default=None, description="带交易所后缀的唯一代码，如 600519.SH")
    name: Optional[str] = None
    v1: ThesisVersion
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)
    v2: ThesisVersion
    diffs: list[ThesisDiff] = Field(default_factory=list)

    @model_validator(mode="after")
    def _thscode_required(self) -> "ParsedThesis":
        # longbridge 约束「标的代码必须标注」：定了标的就必须给完整 thscode
        if self.ticker and not self.thscode:
            raise ValueError("给出了 ticker 就必须给出带交易所后缀的完整 thscode，不允许猜后缀")
        return self


# --------------------------------------------------------------------------
# M3 子问题拆解
# --------------------------------------------------------------------------


class SubQuestion(BaseModel):
    """可验证子问题。五个字段缺一不可，缺了就进不了 SubQuestion，只能进 UnableToDecompose。"""

    id: str
    text: str = Field(description="子问题的自然语言表述")
    metric: str = Field(description="① 指标：要看的量化指标")
    data_source: str = Field(description="② 数据源：从哪取，必须是可以真实调用的")
    time_window: str = Field(description="③ 时间窗：比较哪一段和哪一段")
    decision_rule: str = Field(description="④ 判定规则：什么情况算支持、什么算反对")
    threshold: str = Field(description="④ 附：阈值（可为定性描述，但必须具体到可比）")
    expected_direction: Verdict = Field(
        description="⑤ 期望证据方向：若原命题成立，这条证据应指向哪一态"
    )
    layer: Optional[DecompositionLayer] = Field(
        default=None, description="若属财务分解，标明分解层"
    )
    rationale: str = Field(description="为什么这条能验证命题——本子问题与命题的逻辑连接")

    @field_validator("metric", "data_source", "time_window", "decision_rule", "threshold")
    @classmethod
    def _no_placeholder(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("子问题五字段不可为空")
        if v.strip() in {"-", "N/A", "待定", "无"}:
            raise ValueError(f"字段值 '{v}' 等同于未填——请把该子问题移入无法验证清单")
        return v


class UnableToDecompose(BaseModel):
    """拆解阶段就失败的子问题。显式声明，不静默丢弃。"""

    attempted_question: str
    missing_field: str = Field(description="五字段中缺的是哪一个")
    reason: str
    detail: UnverifiableDetail


class DecompositionResult(BaseModel):
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    unable_to_decompose: list[UnableToDecompose] = Field(default_factory=list)
    skipped_layers: list[DecompositionLayer] = Field(
        default_factory=list, description="本次未覆盖的分解层，必须显式声明原因"
    )
    skipped_layer_reasons: dict[str, str] = Field(default_factory=dict)
    generated_by: Literal["manual_gold_template", "llm_instantiated", "hybrid"] = Field(
        default="hybrid", description="拆解来源。gold 模板手工精写，LLM 只做实例化"
    )


# --------------------------------------------------------------------------
# M4/M5 证据与判定
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    """一条证据。这是整个产品的原子单元，字段缺失则构造失败。"""

    id: str
    sub_question_id: str
    claim: str = Field(description="这条证据陈述的事实，一句话")
    value: Any = Field(default=None, description="指标值。可为数字、字符串或 None")
    display_value: str = Field(description="展示用字符串，含单位")
    provenance: Provenance
    decision_rule_applied: str = Field(description="实际套用的判定规则")
    threshold_applied: str
    verdict: Verdict
    confidence: Confidence
    reasoning: str = Field(description="从数值到三态结论的推理链，一步步写清楚")
    fact_or_logic: Literal["fact", "logic"] = Field(
        default="fact", description="equity-investment-thesis 约束：必须区分事实与逻辑"
    )
    unverifiable: Optional[UnverifiableDetail] = None

    @model_validator(mode="after")
    def _unverifiable_needs_detail(self) -> "Evidence":
        if self.verdict is Verdict.UNVERIFIABLE and self.unverifiable is None:
            raise ValueError(
                "判为无法验证的条目必须附带三问详情（原因分类 / 需要什么 / 何时何地可得）"
            )
        if self.verdict is not Verdict.UNVERIFIABLE and self.unverifiable is not None:
            raise ValueError("非无法验证的条目不应携带 unverifiable 详情")
        return self


class Conflict(BaseModel):
    """证据冲突。强制渲染，不做和稀泥。"""

    sub_question_id: str
    evidence_ids: list[str] = Field(min_length=2)
    nature: str = Field(description="冲突的性质：是口径不同、时点不同，还是真的结论相反")
    resolving_priority: ConflictPriority = Field(description="依据哪个优先维度裁决")
    resolution: str = Field(description="裁决过程与结论")
    residual_uncertainty: str = Field(
        description="裁决后残余的不确定性。不许写「无」——除非确实已完全消解，并说明为何"
    )


# --------------------------------------------------------------------------
# M8 反转条件表（★ 差异化核心）
# --------------------------------------------------------------------------


class FalsificationCondition(BaseModel):
    """反转条件。题目最后一句「明确哪些信息变化会改变当前结论」的结构化答案。"""

    monitored_variable: str = Field(description="监控变量")
    current_value: str = Field(description="当前值（含单位与报告期）")
    trigger_threshold: str = Field(description="触发阈值")
    direction: str = Field(description="变化方向，如「上行突破」")
    flips_sub_question: str = Field(description="翻转哪个子问题的结论")
    marginal_impact: Literal["high", "medium", "low"] = Field(
        description="边际影响力：该变量翻转后，总结论是否跟着翻转"
    )
    next_disclosure: str = Field(description="下次披露时点")
    threshold_basis: str = Field(
        description="阈值依据——为什么是这个数。禁止拍脑袋，必须说明来自历史区间/会计恒等式/同业水准"
    )


# --------------------------------------------------------------------------
# M7 结论
# --------------------------------------------------------------------------


class Conclusion(BaseModel):
    """结论。措辞受限：只用「现有证据支持 / 不支持 / 证据不足」。"""

    verdict: Verdict
    statement: str = Field(description="结论陈述，禁止出现买入/卖出/目标价/预计涨跌幅等措辞")
    support_count: int
    refute_count: int
    unverifiable_count: int
    coverage_note: str = Field(description="覆盖度自评：本次证据覆盖了命题的哪些方面、漏了哪些")
    conflicts: list[Conflict] = Field(default_factory=list)
    falsification_conditions: list[FalsificationCondition] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list, description="已知边界与未做事项")
    disclaimer: str = Field(
        default="本结果基于公开数据对投资命题做可验证性检验，仅供研究参考，不构成任何投资建议。"
    )


# --------------------------------------------------------------------------
# 顶层产物
# --------------------------------------------------------------------------


class ThesisVerification(BaseModel):
    """一次命题验证的完整产物。前端、API、测试都围绕这个对象。"""

    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parsed: ParsedThesis
    decomposition: DecompositionResult
    evidence: list[Evidence] = Field(default_factory=list)
    charts: Optional["Charts"] = Field(
        default=None, description="图表数据。所有点都来自本次运行的证据，不额外取数"
    )
    conclusion: Optional[Conclusion] = None
    data_mode: Literal["live", "fixture", "mixed"] = Field(
        description="本次数据来源模式。fixture 模式必须在 UI 上明示，不得冒充实时"
    )
    data_mode_note: str = Field(description="数据模式的说明与影响")
    errors: list[str] = Field(
        default_factory=list, description="本次运行中发生的失败。失败必须透明，不可静默跳过"
    )


class SeriesPoint(BaseModel):
    """图上的一点。label 是展示用的横轴刻度，值为 None 时该点不从图中跳过而是断线。"""

    label: str
    value: Optional[float] = None


class ValuationChart(BaseModel):
    """估值分位带图。

    扶摇不提供历史估值，序列由本产品用「前复权收盘价 ÷ EPS_TTM」重建，
    因此这张图上必须同时给出重建值与官方 pe_ttm 的对照，以及自校准的偏差——
    否则读者会把它当成行情终端里的官方分位。
    """

    series: list[SeriesPoint]
    latest: Optional[float] = None
    percentile: Optional[float] = None
    bands: dict[str, float] = Field(default_factory=dict, description="p10/p25/p50/p75/p90 分位线")
    official_pe_ttm: Optional[float] = None
    relative_gap: Optional[float] = None
    caliber: str
    note: str


class ProfitChart(BaseModel):
    """逐期同比。同一报告期的收入端与利润端并排，用来暴露「增收不增利」。"""

    periods: list[str] = Field(default_factory=list)
    revenue_yoy: list[Optional[float]] = Field(default_factory=list)
    profit_yoy: list[Optional[float]] = Field(default_factory=list)
    unit: str = "%"


class MarginChart(BaseModel):
    """毛利率与营业成本率的同期序列。两者互为镜像，用来看成本压力落在谁身上。"""

    periods: list[str] = Field(default_factory=list)
    gross_margin: list[Optional[float]] = Field(default_factory=list)
    cost_ratio: list[Optional[float]] = Field(default_factory=list)
    unit: str = "%"


class Charts(BaseModel):
    valuation: Optional[ValuationChart] = None
    profit: Optional[ProfitChart] = None
    margin: Optional[MarginChart] = None


class CompareRequest(BaseModel):
    """横向比较：同一条命题模板套用到多个标的。"""

    raw_text_template: str
    thscodes: list[str] = Field(min_length=2, max_length=5)


class FollowUpRequest(BaseModel):
    """追问：针对某条子问题或证据继续深挖。"""

    run_id: str
    target: Literal["sub_question", "evidence", "conclusion"]
    target_id: str
    question: str


class SavedResearchTask(BaseModel):
    """保存为研究任务（M9）。"""

    task_id: str
    run_id: str
    title: str
    thscode: str
    thesis_text: str
    falsification_conditions: list[FalsificationCondition]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    next_check: str = Field(description="下一次应检查的时点，取自反转条件表中最近的一个")

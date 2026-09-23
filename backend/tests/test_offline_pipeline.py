"""离线全链路测试（第 3–6 层）。

全部使用 `FixtureProvider`：`backend/fixtures/` 下是 4 个标的（002594.SZ / 300750.SZ /
600519.SH / 601318.SH）对扶摇接口的真实调用快照。不联网、不需要凭据。

断言里的数值全部来自实跑，不是从文档抄的。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.providers.base import Err, Ok
from app.schemas import (
    Confidence,
    UnverifiableCategory,
    UnverifiableDetail,
    Verdict,
)
from tests.conftest import (
    CAPTURED_THSCODES,
    CASES,
    EXPECTED,
    evidence_by_id,
    evidence_by_sq,
)

ALL_CASES = sorted(CASES)

# 结构化「取不到」的原因分类：数据源里根本不存在，或公司没披露到该颗粒度。
# 与「数据取到了但落在中性区间」（inconclusive_range）和「接口调用失败」
# （source_unreachable）是不同性质——前者是能力边界，不是本次运行的偶然失败。
STRUCTURAL_GAP_CATEGORIES = {
    UnverifiableCategory.DATA_NOT_EXIST,
    UnverifiableCategory.NOT_DISCLOSED,
}

# 「这一次取数就没成功」的原因分类：这些情况下手里不可能有数
FETCH_FAILURE_CATEGORIES = {
    UnverifiableCategory.SOURCE_UNREACHABLE,
    UnverifiableCategory.PERMISSION_DENIED,
    UnverifiableCategory.PERIOD_NOT_DUE,
}

# 荐股措辞黑名单。命题验证器的结论只能陈述「现有证据支持什么」。
FORBIDDEN_WORDS = ("买入", "卖出", "建议持股", "目标价", "预计涨跌幅", "推荐")

# 阈值依据里必须出现的「出处」标记。空话型依据（「变化显著时需重估」）一条都命不中。
THRESHOLD_BASIS_MARKERS = (
    "分界",      # 会计分界（0%）
    "同上",      # 援引上一条条件的阈值
    "取自",      # 取自模板里的判据
    "阈值",      # 直接说明阈值来源
    "恒等式",    # 会计恒等式
    "支持区间",  # 三态判据的支持区间下沿
    "信息可得性",  # 非数值阈值，而是信息阈值
)

# 允许出现 `**粗体**` 的字段名。后端在这几个字段里用 markdown 强调关键词，
# 前端对应的槽位必须走 RichText 组件渲染成 <strong>。
#
# 这份清单的意义在于：它是一道**双向**的闸门。字段名不在表里却带了 `**`
# → 测试失败，说明这条文案会以「成本率必须**上升**」的形式原样漏给读者；
# 表里的字段前端漏接 RichText → UI 冒烟脚本的 `**` 扫描会抓住。
# 两边任何一侧失守都有测试打红，不靠改代码的人记得。
MARKDOWN_FIELDS = {
    "statement",            # conclusion.statement
    "coverage_note",
    "disclaimer",
    "limitations",
    "nature",               # conflicts[].nature / resolution / residual_uncertainty
    "resolution",
    "residual_uncertainty",
    "reasoning",            # evidence[].reasoning
    "claim",                # evidence[].claim
    "decision_rule_applied",
    "threshold_applied",
    "text",                 # sub_questions[].text / parsed.v1.text / parsed.v2.text
    "decision_rule",        # sub_questions[].decision_rule
    "metric",               # sub_questions[].metric
    "data_source",          # sub_questions[].data_source
    "time_window",          # sub_questions[].time_window
    "rationale",            # sub_questions[].rationale
    "threshold_basis",      # falsification_conditions[].*
    "monitored_variable",
    "trigger_threshold",
    "flips_sub_question",
    "type_rationale",       # parsed.type_rationale
    "reason",               # parsed.diffs[].reason
    "before",               # parsed.diffs[].before / .after
    "after",
    "question",             # clarifications[].question
    "why_it_matters",
    "assumption",
    "answer",               # clarifications[].answer（用户在澄清环节的原话）
    "impact",               # clarifications[].impact（该回答改变了下游什么）
    "what_is_needed",       # unverifiable.*
    "where_to_get",
    "failure_evidence",
    "skipped_layer_reasons",
    "errors",               # 顶层失败清单
}


def _walk_markdown_paths(node, path=()):
    """递归收集所有含 `**` 的字符串，回报它的 JSON 路径。"""
    if isinstance(node, str):
        if "**" in node:
            yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_markdown_paths(v, path + (str(k),))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk_markdown_paths(v, path + (f"[{i}]",))


# ==========================================================================
# 第 3 层：离线全链路
# ==========================================================================


@pytest.mark.parametrize("cid", ALL_CASES)
def test_run_completes_without_errors(offline_runs, cid):
    """四个预设命题的链路都必须零错误跑完。"""
    tv = offline_runs[cid]
    assert tv.errors == [], f"{cid} 的链路留下了失败记录：{tv.errors}"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_resolved_target_matches_expected(offline_runs, cid):
    """标的消歧结果必须与实跑确认的一致——消歧错了一切下游都无意义。"""
    tv = offline_runs[cid]
    exp = EXPECTED[cid]
    assert tv.parsed.ticker == exp["ticker"]
    assert tv.parsed.thscode == exp["thscode"]
    assert tv.parsed.name == exp["name"]


@pytest.mark.parametrize("cid", ALL_CASES)
def test_conclusion_verdict_is_one_of_three_states(offline_runs, cid):
    tv = offline_runs[cid]
    assert tv.conclusion is not None
    assert isinstance(tv.conclusion.verdict, Verdict)
    assert tv.conclusion.verdict.value in {"support", "refute", "unverifiable"}
    assert tv.conclusion.verdict.value == EXPECTED[cid]["verdict"]


@pytest.mark.parametrize("cid", ALL_CASES)
def test_every_evidence_obeys_unverifiable_contract(offline_runs, cid):
    """全链路产出的每一条证据都必须满足 `_unverifiable_needs_detail` 的约束。

    schema 已经保证了构造期的约束，这里验证的是运行期：没有任何一条证据是
    「判了无法验证但没写清楚为什么」的。
    """
    tv = offline_runs[cid]
    assert tv.evidence, "证据列表不能为空"
    for ev in tv.evidence:
        if ev.verdict is Verdict.UNVERIFIABLE:
            assert ev.unverifiable is not None, f"{ev.id} 判为无法验证却没有三问详情"
            assert ev.unverifiable.what_is_needed.strip()
            assert ev.unverifiable.where_to_get.strip()
            assert ev.unverifiable.failure_evidence.strip()
            assert ev.unverifiable.category in set(UnverifiableCategory)
            # 不编造数据：如果这一路的取数本身就失败了（接口报错/无权限/报告期未到），
            # 就不允许出现任何数值。「数据取到了但落在中性区间」和「能力边界声明」
            # 两类可以带值，因为它们手里确实有一个算出来的代理指标。
            if ev.unverifiable.category in FETCH_FAILURE_CATEGORIES:
                assert ev.value is None, f"{ev.id} 取数失败却带了数值 {ev.value!r}"
        else:
            assert ev.unverifiable is None, f"{ev.id} 非无法验证却带了无法验证详情"
        assert ev.reasoning.strip(), f"{ev.id} 缺少推理链"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_every_evidence_is_traceable(offline_runs, cid):
    """每个进入结论的数字都要挂得上溯源（source / endpoint / report_period / caliber / unit）。"""
    tv = offline_runs[cid]
    for ev in tv.evidence:
        p = ev.provenance
        for field in ("source", "endpoint", "report_period", "caliber", "unit"):
            assert getattr(p, field).strip(), f"{ev.id} 的溯源字段 {field} 为空"
        assert p.fetched_at is not None


@pytest.mark.parametrize("cid", ALL_CASES)
def test_counts_add_up(offline_runs, cid):
    tv = offline_runs[cid]
    c = tv.conclusion
    assert c.support_count + c.refute_count + c.unverifiable_count == len(tv.evidence)
    assert c.disclaimer.strip()


@pytest.mark.parametrize("cid", ALL_CASES)
def test_fixture_mode_is_declared(offline_runs, cid):
    """fixture 模式必须在产物上明示，不得冒充实时。"""
    tv = offline_runs[cid]
    assert tv.data_mode == "fixture"
    assert tv.data_mode_note.strip()


@pytest.mark.parametrize("cid", ALL_CASES)
def test_evidence_snapshots_are_marked_as_frozen(offline_runs, cid):
    """证据的溯源要能看出数据来自冻结快照，而不是实时接口。"""
    tv = offline_runs[cid]
    sources = {ev.provenance.source for ev in tv.evidence}
    assert sources, "证据必须有来源"
    assert all("扶摇" in s for s in sources), f"出现非预期来源：{sources}"
    assert any("冻结快照" in s or "能力边界" in s for s in sources)


# --- 案例一：贵州茅台（背离型，应判不支持） --------------------------------


def test_maotai_is_refuted_by_income_without_profit(offline_runs):
    """「估值回落但基本面未恶化」在茅台上不成立：估值侧成立，但利润端已恶化。

    这是命题里最典型的反例——营收还在涨，归母净利润已经掉了，即「增收不增利」。
    """
    tv = offline_runs["maotai_divergence"]
    assert tv.parsed.thesis_type.value == "divergence"
    assert tv.conclusion.verdict is Verdict.REFUTE

    rev = evidence_by_sq(tv, "SQ-03")
    prof = evidence_by_sq(tv, "SQ-04")
    assert rev.verdict is Verdict.SUPPORT
    assert prof.verdict is Verdict.REFUTE
    assert rev.value == pytest.approx(1.47), "营业收入同比应为 +1.47%"
    assert prof.value == pytest.approx(-1.95), "归母净利润同比应为 -1.95%"
    assert rev.value > 0 > prof.value

    # 估值侧确实回落（否则这题会走「前提不成立」那条分支，结论理由完全不同）
    assert evidence_by_sq(tv, "SQ-01").verdict is Verdict.SUPPORT

    # 增收不增利必须作为冲突显式留在结论里，不许和稀泥
    assert tv.conclusion.conflicts, "增收不增利必须产出一条冲突"
    pair = [c for c in tv.conclusion.conflicts if set(c.evidence_ids) == {"EV-SQ-03", "EV-SQ-04"}]
    assert len(pair) == 1, "应收录 SQ-03 与 SQ-04 的冲突"
    conflict = pair[0]
    assert "同口径" in conflict.nature
    assert conflict.residual_uncertainty.strip()
    assert "无法完全消解" in conflict.residual_uncertainty
    # 结论以归母净利润为判定主证据
    assert "归母净利润" in conflict.resolution
    assert "不支持" in tv.conclusion.statement


def test_maotai_cash_quality_is_inconclusive_not_refuted(offline_runs):
    """现金含量 0.75 倍落在 0.5–0.8 的中性区间，判为「有实质内容的无法验证」。

    这不是取数失败：数据完整、口径一致，是判定规则在该区间不做方向性判断。
    """
    tv = offline_runs["maotai_divergence"]
    cash = evidence_by_sq(tv, "SQ-05")
    assert cash.verdict is Verdict.UNVERIFIABLE
    assert cash.value == pytest.approx(0.75)
    assert cash.unverifiable.category is UnverifiableCategory.INCONCLUSIVE_RANGE
    assert "中性区间" in cash.unverifiable.failure_evidence


# --- 案例二：中国平安（背离型，应判支持） ---------------------------------


def test_pingan_is_supported(offline_runs):
    tv = offline_runs["pingan_divergence"]
    assert tv.conclusion.verdict is Verdict.SUPPORT
    assert tv.conclusion.refute_count == 0, "支持态结论里不应出现反对证据"
    assert tv.conclusion.support_count >= 4

    val = evidence_by_sq(tv, "SQ-01")
    assert val.verdict is Verdict.SUPPORT
    assert "21.3 分位" in val.display_value, "估值应处于历史低分位"

    for sq in ("SQ-03", "SQ-04"):
        assert evidence_by_sq(tv, sq).verdict is Verdict.SUPPORT
    assert evidence_by_sq(tv, "SQ-03").value == pytest.approx(15.01)
    assert evidence_by_sq(tv, "SQ-04").value == pytest.approx(36.06)


# --- 案例三：宁德时代 · 归因型 --------------------------------------------


def test_catl_attribution_is_refuted_on_gross_margin(offline_runs):
    tv = offline_runs["catl_attribution"]
    assert tv.parsed.thesis_type.value == "attribution"
    assert tv.conclusion.verdict is Verdict.REFUTE

    # 核心判据（非经常性损益）通过——排除了「靠一次性收益」这条最直接的反例
    core = evidence_by_sq(tv, "SQ-03")
    assert core.verdict is Verdict.SUPPORT
    assert float(core.value) <= 2.0, "核心判据按规则是 ≤2pp 才支持"

    # 被支撑侧证伪：毛利率端同比下滑
    gm = evidence_by_sq(tv, "SQ-02")
    assert gm.verdict is Verdict.REFUTE
    assert gm.value < 0

    # 结论措辞必须与实际三态一致：曾经写死过「收入端与毛利率端未能提供支持」，
    # 而收入端其实是支持态，导致结论陈述与证据卡自相矛盾。
    assert evidence_by_sq(tv, "SQ-01").verdict is Verdict.SUPPORT
    assert "收入端" not in tv.conclusion.statement.split("但")[-1]


def test_catl_attribution_declares_structurally_skipped_layers(offline_runs):
    """量价拆分做不到就要显式声明，而不是用「量价齐升」这类无数据支撑的措辞带过。"""
    tv = offline_runs["catl_attribution"]
    vol_price = evidence_by_sq(tv, "SQ-07")
    assert vol_price.verdict is Verdict.UNVERIFIABLE
    assert vol_price.unverifiable.category is UnverifiableCategory.DATA_NOT_EXIST
    assert vol_price.value is None
    assert "结构性不可得" in vol_price.reasoning


# --- 案例四：宁德时代 · 传导型（无法验证的主场） ---------------------------


def test_catl_transmission_is_unverifiable_for_structural_reasons(offline_runs):
    """传导型命题应判无法验证，且失败原因是「数据源里没有」而非「本次取数失败」。"""
    tv = offline_runs["catl_transmission"]
    assert tv.parsed.thesis_type.value == "transmission"
    assert tv.conclusion.verdict is Verdict.UNVERIFIABLE

    structural = [
        ev
        for ev in tv.evidence
        if ev.verdict is Verdict.UNVERIFIABLE and ev.unverifiable.category in STRUCTURAL_GAP_CATEGORIES
    ]
    assert len(structural) >= 3, (
        f"应至少 3 条证据属「结构性不可得」，实际 {len(structural)} 条："
        f"{[(ev.sub_question_id, ev.unverifiable.category.value) for ev in structural]}"
    )

    # 根节点（外部变量）与前提（主营构成）都必须是结构化缺失，而不是接口报错
    assert evidence_by_sq(tv, "SQ-01").unverifiable.category is UnverifiableCategory.NOT_DISCLOSED
    assert evidence_by_sq(tv, "SQ-05").unverifiable.category is UnverifiableCategory.DATA_NOT_EXIST

    assert "能力边界" in tv.conclusion.statement
    # 结论不允许给方向性判断
    assert "不足" in tv.conclusion.statement


def test_catl_transmission_falsification_is_about_information_availability(offline_runs):
    """传导型的反转条件不是数值阈值，而是「拿到什么信息会改变结论」——但必须写出来。"""
    tv = offline_runs["catl_transmission"]
    conds = tv.conclusion.falsification_conditions
    avail = [c for c in conds if "信息可得性" in c.direction]
    assert len(avail) == 1
    cond = avail[0]
    assert "分部收入" in cond.monitored_variable
    assert cond.threshold_basis.strip()
    assert "不适用" in cond.next_disclosure
    # 数值型条件也必须给出——传导型不是「无从下手」，成本端是能监控的。
    assert [c for c in conds if "信息可得性" not in c.direction], (
        "传导型除信息可得性外，还应有一条可监控的数值条件（成本端）"
    )


# ==========================================================================
# 第 4 层：一致性（证据卡与反转条件表必须是同一个数）
# ==========================================================================

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
# current_value 形如 "+1.47%（2026Q2 vs 2025Q2）"：数字在前，报告期在括号里
_PERIOD_RE = re.compile(r"（([^（）]+)）\s*$")


def _primary_number(current_value: str) -> str | None:
    """取 current_value 里括号前的那个数值，去掉正号后返回。"""
    head = current_value.split("（", 1)[0]
    m = _NUMBER_RE.search(head)
    if m is None:
        return None
    return m.group(0).lstrip("+")


@pytest.mark.parametrize(
    "cid",
    [
        pytest.param("maotai_divergence", id="maotai"),
        pytest.param("pingan_divergence", id="pingan"),
        pytest.param("catl_transmission", id="catl-transmission"),
        # 现金含量末位不一致的缺陷已修复（反转条件表改为直接引用证据卡的 display_value），
        # xfail 标记已按要求删除，本条转为正常断言。
        pytest.param("catl_attribution", id="catl-attribution"),
    ],
)
def test_falsification_values_trace_back_to_evidence(offline_runs, cid):
    """反转条件表里的每个数值都必须能在证据卡的展示值里找到。

    这条断言针对的是一类具体的复发缺陷：证据卡与反转条件表各走一条计算/格式化路径，
    同一个指标在同一页上出现两个数字（或两个报告期）。
    """
    tv = offline_runs[cid]
    displays = [ev.display_value for ev in tv.evidence]
    checked = 0
    for cond in tv.conclusion.falsification_conditions:
        number = _primary_number(cond.current_value)
        if number is None:
            continue
        checked += 1
        assert any(number in d for d in displays), (
            f"反转条件「{cond.monitored_variable}」的当前值 {cond.current_value!r} "
            f"中的 {number} 在任何一条证据的展示值里都找不到；证据展示值={displays}"
        )
    if tv.parsed.thesis_type.value != "transmission":
        # 传导型的反转条件是「信息可得性」阈值而不是数值阈值，没有数字可对
        assert checked > 0, "反转条件表里至少应有一条带数值的条件"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_falsification_period_matches_source_evidence(offline_runs, cid):
    """反转条件表标注的报告期必须能在证据的溯源里找到。

    这条防的是「证据卡用最新季报、反转条件表用去年年报」——同一个指标两个报告期。
    """
    tv = offline_runs[cid]
    periods = [ev.provenance.report_period for ev in tv.evidence]
    checked = 0
    for cond in tv.conclusion.falsification_conditions:
        m = _PERIOD_RE.search(cond.current_value)
        if m is None:
            continue
        period = m.group(1)
        checked += 1
        assert any(period == p or period in p for p in periods), (
            f"反转条件「{cond.monitored_variable}」标注报告期 {period!r}，"
            f"但没有任何证据的溯源报告期与之对应；证据报告期={periods}"
        )
    if tv.parsed.thesis_type.value != "transmission":
        assert checked > 0, "反转条件表里至少应有一条带报告期的条件"


@pytest.mark.parametrize("cid", ["maotai_divergence", "pingan_divergence"])
def test_revenue_and_profit_evidence_share_the_same_period(offline_runs, cid):
    """同一条结论里的同比证据必须同报告期。

    回归：曾经 SQ-03 从季报取数、SQ-04 从年报取数，或者反过来漏掉最新季报，
    导致证据卡比实际数据晚三个季度。
    """
    tv = offline_runs[cid]
    rev = evidence_by_sq(tv, "SQ-03")
    prof = evidence_by_sq(tv, "SQ-04")
    assert rev.provenance.report_period == prof.provenance.report_period
    assert rev.provenance.report_period, "报告期不能为空"
    # 应取到最新的季度同比，而不是陈旧的年报
    assert "Q" in rev.provenance.report_period, (
        f"同比证据应取最新季报口径，实际报告期为 {rev.provenance.report_period!r}"
    )


def test_cash_row_matches_evidence_card(offline_runs):
    """现金含量那一行曾走过两条格式化路径，末位与证据卡差一位（1.84 vs 1.85）。

    现在反转条件表直接引用证据卡的 display_value，这条断言由「已知缺陷」转为回归护栏。
    """
    tv = offline_runs["catl_attribution"]
    cash = evidence_by_sq(tv, "SQ-05")
    row = next(
        c for c in tv.conclusion.falsification_conditions if "现金含量" in c.monitored_variable
    )
    number = _primary_number(row.current_value)
    assert number is not None
    assert number in cash.display_value, (
        f"现金含量证据卡展示 {cash.display_value!r}，反转条件表显示 {row.current_value!r}"
    )


def test_attribution_falsification_rows_bind_to_the_right_sub_question(offline_runs):
    """反转条件表曾按子问题 ID 硬编码取值，未按命题类型区分。

    归因型的 SQ-03 是非经常性损益、SQ-04 是期间费用率，被当成营业收入同比与
    归母净利润同比写进了反转条件表，真正的营收同比 +54.80% 在表里根本不出现。
    现在按命题类型分别取 ID，本条守住这个绑定关系。
    """
    tv = offline_runs["catl_attribution"]
    rev = evidence_by_sq(tv, "SQ-01")  # 归因型：SQ-01 才是营业收入同比
    assert float(rev.value) == pytest.approx(54.80)

    row = next(
        c for c in tv.conclusion.falsification_conditions if c.monitored_variable == "营业收入同比增速"
    )
    assert "54.80" in row.current_value, (
        f"反转条件表把「营业收入同比增速」写成了 {row.current_value!r}，"
        f"而证据卡 SQ-01 的营业收入同比是 {rev.value}"
    )


def test_cyclicality_conflict_is_not_produced_for_attribution(offline_runs):
    """周期性冲突只在背离型成立——那条冲突的两个端点分别是「估值分位」与「毛利率离散度」。

    归因型里 SQ-06 是「少数股东损益占净利润」，与周期性无关；
    冲突检测曾只按子问题 ID 绑定证据，把这条证据当成毛利率离散度写进了冲突正文。
    现在 detect_conflicts 必须显式接收命题类型，本条守住这个前提。
    """
    tv = offline_runs["catl_attribution"]
    cyclical = [
        c
        for c in tv.conclusion.conflicts
        if "EV-SQ-06" in c.evidence_ids and "周期性" in c.nature
    ]
    assert cyclical == [], (
        f"归因型不应产出周期性冲突，实际产出 {len(cyclical)} 条："
        f"{[c.nature[:60] for c in cyclical]}"
    )


def test_transmission_has_no_conflict_to_report(offline_runs):
    """传导型的证据集里没有任何一对构成冲突，冲突应为空。"""
    tv = offline_runs["catl_transmission"]
    assert tv.conclusion.conflicts == [], (
        f"传导型不应产出冲突，实际产出 {len(tv.conclusion.conflicts)} 条："
        f"{[(c.sub_question_id, c.nature[:40]) for c in tv.conclusion.conflicts]}"
    )


@pytest.mark.parametrize("cid", ALL_CASES)
def test_falsification_conditions_are_monitorable(offline_runs, cid):
    """反转条件不许写「若基本面变化需重新评估」这种废话：每条都要有监控变量、
    当前值、触发阈值、方向、翻转对象、边际影响力、下次披露时点和阈值依据。
    """
    tv = offline_runs[cid]
    conds = tv.conclusion.falsification_conditions
    assert conds, "反转条件表不能为空"
    for cond in conds:
        assert cond.monitored_variable.strip()
        assert cond.current_value.strip()
        assert cond.trigger_threshold.strip()
        assert cond.direction.strip()
        assert cond.flips_sub_question.strip()
        assert cond.marginal_impact in {"high", "medium", "low"}
        assert cond.next_disclosure.strip()
        assert cond.threshold_basis.strip(), f"「{cond.monitored_variable}」没写阈值依据"
        # 阈值依据不能是「显著变化」「经验值」这类空话：要么援引判据/会计分界/
        # 上一个条件的阈值（「同上」），要么是信息可得性阈值。
        assert any(marker in cond.threshold_basis for marker in THRESHOLD_BASIS_MARKERS), (
            f"「{cond.monitored_variable}」的阈值依据看不出出处：{cond.threshold_basis!r}"
        )


# ==========================================================================
# 第 5 层：失败路径（缺数据必须报 Err，不许返回空列表或编造）
# ==========================================================================


def test_unknown_ticker_search_returns_err(provider):
    """fixtures 里没有的标的，检索必须返回 Err，而不是空列表。"""
    res = provider.search_ticker("招商银行")
    assert isinstance(res, Err), f"未捕获的标的应返回 Err，实际 {type(res).__name__}"
    assert res.ok is False
    assert res.detail.category is UnverifiableCategory.SOURCE_UNREACHABLE
    assert "招商银行" in res.detail.failure_evidence
    assert res.detail.what_is_needed.strip()
    assert res.detail.where_to_get.strip()


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: p.income_statements("000001.SZ", "quarterly", 12), id="income"),
        pytest.param(lambda p: p.income_statements("000001.SZ", "annual", 12), id="income-annual"),
        pytest.param(lambda p: p.balance_sheets("000001.SZ", "annual", 12), id="balance"),
        pytest.param(lambda p: p.cash_flow_statements("000001.SZ", "annual", 12), id="cashflow"),
        pytest.param(lambda p: p.cash_flow_statements("000001.SZ", "quarterly", 12), id="cashflow-q"),
        pytest.param(lambda p: p.valuation_snapshot(["000001.SZ"]), id="valuation"),
        pytest.param(lambda p: p.price_snapshot(["000001.SZ"]), id="price"),
        pytest.param(
            lambda p: p.price_historical("000001.SZ", 0, 1_700_000_000_000, "forward"),
            id="prices",
        ),
        pytest.param(lambda p: p.financial_indicators("000001.SZ", "2026-2"), id="indicators"),
    ],
)
def test_missing_fixture_route_returns_err_not_empty_list(provider, call):
    """每一路取数在缺快照时都必须回 Err。

    这是本项目「失败必须透明」的落地方式：拿不到 Ok 就只剩「带三问的 Err」这一条路，
    不存在「悄悄跳过」或「返回空数组当作没有异常」的代码路径。
    """
    res = call(provider)
    assert isinstance(res, Err), f"缺快照时应返回 Err，实际 {type(res).__name__}"
    assert isinstance(res.detail, UnverifiableDetail)
    assert res.endpoint.strip()
    assert "fixture 文件不存在" in res.detail.failure_evidence
    # 明确拒绝用模拟数据顶替
    assert "不用模拟数据顶替" in res.detail.failure_evidence


def test_unknown_target_chain_reports_failure_without_fabricating(provider):
    """整条链路遇到未捕获的标的时：如实报错、无结论、无证据，而不是编一张证据卡。"""
    from app.engine.run import run_verification

    res = run_verification(
        raw_text="我认为招商银行估值已经回落但基本面并没有恶化",
        provider=provider,
        data_mode="fixture",
        data_mode_note="pytest 离线快照模式",
    )
    assert res.errors, "未能确定标的时必须留下失败记录"
    assert any("招商银行" in e for e in res.errors)
    assert res.conclusion is None, "标的都没确定，不允许给出结论"
    assert res.evidence == [], "标的都没确定，不允许产出任何证据"


def test_provider_without_any_fixture_returns_err(tmp_path):
    """连 fixtures 目录都不存在时，每一路取数仍是 Err，且 available() 如实为空。"""
    from app.providers.fixture import FixtureProvider

    empty = FixtureProvider(root=tmp_path / "不存在的目录")
    assert empty.available() == {}
    assert isinstance(empty.search_ticker("贵州茅台"), Err)
    assert isinstance(empty.income_statements("600519.SH", "quarterly", 12), Err)
    assert isinstance(empty.valuation_snapshot(["600519.SH"]), Err)


def test_captured_fixtures_are_available(provider):
    """对照组：4 个标的的快照确实在，否则上面的 Err 断言可能只是路径写错。"""
    available = provider.available()
    assert set(available) == {code.replace(".", "_") for code in CAPTURED_THSCODES}
    for code in CAPTURED_THSCODES:
        assert isinstance(provider.search_ticker(code), Ok)
        assert isinstance(provider.income_statements(code, "quarterly", 12), Ok)
        assert isinstance(provider.price_historical(code, 0, 1_700_000_000_000, "forward"), Ok)


# ==========================================================================
# 第 6 层：渲染契约
# ==========================================================================


@pytest.mark.parametrize("cid", ALL_CASES)
def test_markdown_emphasis_stays_inside_rendered_fields(offline_runs, cid):
    """带 `**` 的文案只能出现在前端走 RichText 渲染的字段里。

    这条测试是一次真实泄漏逼出来的：传导型命题的判定规则里写着
    「成本率必须**上升**且毛利率下降 >1pp」，而拆解面板当时是裸渲染 `{s.decision_rule}`，
    于是屏幕上原样出现了两个星号。后端写 markdown、前端不认识 markdown，
    这种错不会报异常、不会让页面崩，只会静静地让读者看到一串符号。

    所以这里守的不是「有没有 `**`」，而是「`**` 出现在哪」——
    MARKDOWN_FIELDS 之外的任何位置带着 `**` 都是 bug，
    要么把文案里的星号去掉，要么把那个槽位接上 RichText。
    """
    payload = offline_runs[cid].model_dump()
    offenders = []
    for path, text in _walk_markdown_paths(payload):
        # 路径最后一节是字段名；数组下标形如 "[0]"，往回找最近的非下标节点
        field = next((p for p in reversed(path) if not p.startswith("[")), "")
        if field not in MARKDOWN_FIELDS:
            offenders.append((".".join(path), text[:70]))
    assert not offenders, (
        f"{cid} 以下字段带 `**` 但不在 MARKDOWN_FIELDS 里，前端不会渲染它，"
        f"读者会看到星号本身：\n" + "\n".join(f"  {p} → {t}" for p, t in offenders)
    )


def test_markdown_fields_all_appear_in_frontend_source():
    """清单里的每个字段，前端源码里必须真的提到过。

    这条守的是清单的另一侧：MARKDOWN_FIELDS 允许字段携带 `**`，
    而这个许可只有在「前端确实会渲染它」时才成立。一个前端根本没读过的字段
    留在清单里，等于给未来的文案开了一张空头支票——
    上面那条正向测试会放它过去，读者却在屏幕上看到两个星号。

    这里比对的是源码里出现过字段名，而不是 RichText 的调用点：
    后者要解析 JSX，脆弱且容易误报；前者已经足够挡住「清单里写了、
    前端压根没这个字段」这类失守，而真正会漏给读者的那类问题
    由「正向测试 + UI 冒烟脚本的 DOM 扫描」两条一起兜住。
    """
    src_root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    assert src_root.is_dir(), f"找不到前端源码目录：{src_root}"
    src = "\n".join(p.read_text(encoding="utf-8") for p in src_root.rglob("*.jsx"))

    missing = sorted(f for f in MARKDOWN_FIELDS if f not in src)
    assert not missing, (
        f"MARKDOWN_FIELDS 里这些字段在前端源码中从未出现：{missing}。"
        f"前端不渲染的字段不该出现在这里——它一旦真的带了 `**`，读者会看到星号本身。"
    )


# ==========================================================================
# 第 7 层：合规边界
# ==========================================================================


@pytest.mark.parametrize("cid", ALL_CASES)
def test_no_stock_recommendation_wording(offline_runs, cid):
    """结论陈述与全部证据的推理链里，不得出现荐股措辞。"""
    tv = offline_runs[cid]
    texts = [("conclusion.statement", tv.conclusion.statement)]
    texts += [(f"evidence[{ev.id}].reasoning", ev.reasoning) for ev in tv.evidence]
    texts += [(f"evidence[{ev.id}].claim", ev.claim) for ev in tv.evidence]
    texts += [(f"coverage_note", tv.conclusion.coverage_note)]

    for where, text in texts:
        for word in FORBIDDEN_WORDS:
            assert word not in text, f"{cid} 的 {where} 出现荐股措辞「{word}」：{text}"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_disclaimer_is_present_and_scoped(offline_runs, cid):
    tv = offline_runs[cid]
    disclaimer = tv.conclusion.disclaimer
    assert "不构成任何投资建议" in disclaimer


@pytest.mark.parametrize("cid", ALL_CASES)
def test_limitations_disclose_the_rebuilt_caliber(offline_runs, cid):
    """重建口径与数据源能力边界必须写在结论的适用边界里，不能只藏在代码注释里。"""
    tv = offline_runs[cid]
    limitations = " ".join(tv.conclusion.limitations)
    assert limitations.strip()
    assert "重建" in limitations
    assert "volume" in limitations or "产销量" in limitations


# ==========================================================================
# 第 8 层：拆解结构 —— 拆得出就要证得了
# ==========================================================================


@pytest.mark.parametrize("cid", ALL_CASES)
def test_decomposition_declares_skipped_layers(offline_runs, cid):
    """做不到的分解层必须显式列出并写明原因，不能默认跳过。"""
    from app.schemas import DecompositionLayer

    tv = offline_runs[cid]
    decomp = tv.decomposition
    assert decomp.sub_questions, "子问题列表不能为空"
    assert decomp.unable_to_decompose == [], "模板里的子问题都应通过五字段校验"
    unsupported = {
        DecompositionLayer.VOLUME,
        DecompositionLayer.RATE,
        DecompositionLayer.MIX,
        DecompositionLayer.FX,
    }
    assert unsupported.issubset(set(decomp.skipped_layers)), (
        f"未显式声明跳过的分解层，实际 {[l.value for l in decomp.skipped_layers]}"
    )
    for layer in decomp.skipped_layers:
        assert decomp.skipped_layer_reasons.get(layer.value), f"{layer.value} 没写跳过原因"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_every_sub_question_has_an_executor(offline_runs, cid):
    """模板里的每条子问题都必须有对应执行器，且真的产出了证据。"""
    from app.engine.run import EXECUTOR_SETS

    tv = offline_runs[cid]
    execs = EXECUTOR_SETS[tv.parsed.thesis_type]
    template_ids = [sq.id for sq in tv.decomposition.sub_questions]
    assert template_ids, "模板没有子问题"

    for sq_id in template_ids:
        assert sq_id in execs, f"子问题 {sq_id} 没有执行器——拆得出却证不了"
    produced = {ev.sub_question_id for ev in tv.evidence}
    assert set(template_ids) == produced, (
        f"证据覆盖的子问题与模板不一致：模板={sorted(template_ids)}，实际={sorted(produced)}"
    )
    for ev in tv.evidence:
        assert ev.id == f"EV-{ev.sub_question_id}"


@pytest.mark.parametrize("cid", ALL_CASES)
def test_confidence_is_declared_on_every_evidence(offline_runs, cid):
    tv = offline_runs[cid]
    for ev in tv.evidence:
        assert isinstance(ev.confidence, Confidence)
        assert ev.fact_or_logic in {"fact", "logic"}
        assert ev.claim.strip()
        assert ev.display_value.strip()
        assert ev.decision_rule_applied.strip()
        assert ev.threshold_applied.strip()


# ==========================================================================
# 第 10 层：结论、证据卡与反转条件表必须讲同一件事
#
# 这一层是补一轮审计发现的四类「说了它没做的事」时长出来的。它们的共同形状是：
# **证据卡上的状态变了，别处的措辞没跟着变**。这类错不抛异常、不崩页面，
# 只是让读者读到一个与屏幕另一半相矛盾的句子——最难靠人工发现的那一类。
# ==========================================================================


def _row(tv, keyword: str):
    """按监控变量的关键词取反转条件行；取不到直接失败，不返回 None 让断言静默通过。"""
    hits = [c for c in tv.conclusion.falsification_conditions if keyword in c.monitored_variable]
    assert len(hits) == 1, f"含「{keyword}」的反转条件行为 {len(hits)} 条，期望恰好 1 条"
    return hits[0]


@pytest.mark.parametrize("cid", ALL_CASES)
def test_cyclicality_claim_matches_the_cyclicality_row(offline_runs, cid):
    """结论里关于周期性的那句话，必须与反转条件表里**有没有**周期性那一行一致。

    实测的错误：中国平安的结论无条件地写着「本结论的适用边界受周期性检测结果约束，
    详见冲突与反转条件」，而该例的反转条件表里根本没有周期性这一行
    （SQ-06 的毛利率序列不足 4 期算不出离散度，本表对无数值的子问题不生成行），
    冲突块也因为 0 条冲突而整块不渲染。读者被指向一个不存在的小节。

    注意方向：**结论说「没检测」→ 表里就不能有那一行**；反过来不成立——
    结论被判为不支持时，估值分位已不承载论证，周期性自然不必再提，
    这时表里留着那一行是对的（它是给未来的监控项）。
    """
    tv = offline_runs[cid]
    stmt = tv.conclusion.statement
    has_row = any("周期性" in c.monitored_variable for c in tv.conclusion.falsification_conditions)
    if "未能完成周期性检测" in stmt:
        assert not has_row, "结论说周期性检测没做成，表里却摆着一行周期性监控——两者不可能同时为真"
    if "受周期性检测结果约束" in stmt or "存在周期特征" in stmt:
        assert has_row, "结论声称周期性检测约束了本结论，但反转条件表里没有那一行可看"
    # 背离型且结论为「支持」时，估值分位正是论证的支点，必须交代周期性的处置方式。
    if tv.parsed.thesis_type.value == "divergence" and tv.conclusion.verdict is Verdict.SUPPORT:
        assert "周期性" in stmt, "支持结论建立在估值分位上，却没有交代周期性检测的结果"


def test_pingan_conclusion_does_not_promise_cyclicality_it_never_computed(offline_runs):
    """中国平安：SQ-06 取数成功，但保险公司的利润表里没有 operating_costs 这一行，
    毛利率序列凑不满 4 期，周期性**根本没检测**。

    修复前，结论写的是「本结论的适用边界受周期性检测结果约束」——它让读者以为
    周期性已经查过、只是加了限制条件，而事实是这一层完全没有设防。方向正好反了：
    前者读作「结论更稳」，后者才是「结论有一处没排雷」。
    """
    tv = offline_runs["pingan_divergence"]
    assert tv.conclusion.verdict is Verdict.SUPPORT
    assert "未能完成周期性检测" in tv.conclusion.statement
    assert "未设防" in tv.conclusion.statement
    assert not any(
        "周期性" in c.monitored_variable for c in tv.conclusion.falsification_conditions
    )


@pytest.mark.parametrize("cid", ALL_CASES)
def test_source_unreachable_cards_really_had_a_failed_fetch(offline_runs, cid):
    """判为「数据源调用失败」的证据卡，溯源里必须**真的**没有一次成功的取数。

    实测的错误：中国平安的 SQ-06 取数完全成功（24 个字段），只是拿不到毛利率这个口径，
    卡上的失败原因却写着「未找到 financial_indicators 的取数记录（既无成功返回也无失败记录）」。
    这句话把「这个口径在该标的的报表里不存在」误报成「这次调用出了故障」——
    两者的后续动作完全相反：前者要换数据源，后者重试即可。
    """
    tv = offline_runs[cid]
    for ev in tv.evidence:
        d = ev.unverifiable
        if d is None or d.category is not UnverifiableCategory.SOURCE_UNREACHABLE:
            continue
        assert "/api/" not in ev.provenance.endpoint, (
            f"{ev.sub_question_id} 判为「数据源调用失败」，但溯源里写着一个成功的接口 "
            f"{ev.provenance.endpoint}——自相矛盾"
        )
        assert "未找到" in d.failure_evidence


def test_unverifiable_card_does_not_deny_a_fetch_that_succeeded(offline_runs):
    """上一条的具体案例：平安 SQ-06 的失败原因必须说清**真实原因**。"""
    ev = evidence_by_sq(offline_runs["pingan_divergence"], "SQ-06")
    assert ev.verdict is Verdict.UNVERIFIABLE
    assert ev.unverifiable is not None
    assert "未找到" not in ev.unverifiable.failure_evidence
    assert "取数成功" in ev.unverifiable.failure_evidence
    assert "不是取数故障" in ev.unverifiable.failure_evidence
    # 三问之外的这一条：溯源要能看到失败/成功的是哪个接口，否则读者无从复核。
    assert "/api/" in ev.provenance.endpoint


def test_transmission_coverage_note_does_not_call_a_refutation_evidence(offline_runs):
    """宁德时代传导型：覆盖度说明此前写死「成本端与成本转嫁能力两条子问题提供了间接证据」。

    实测下来，SQ-02 是**反对**态（营业成本率累计变动 −2.01pp，方向与「上游涨价」相反），
    SQ-03 则因窗口内成本率并未上升而根本判不出来。同一句话错了两处：
    把一条反对证据说成「间接证据」，又把一条没跑出结论的证据说成「提供了证据」。
    """
    tv = offline_runs["catl_transmission"]
    note = tv.conclusion.coverage_note
    assert evidence_by_sq(tv, "SQ-02").verdict is Verdict.REFUTE
    assert evidence_by_sq(tv, "SQ-03").verdict is Verdict.UNVERIFIABLE
    assert "间接证据" not in note, "SQ-02 是反对态、SQ-03 无法判定，都不构成「间接证据」"
    assert "并未发生显著变化" in note, "SQ-02 的实际状态（成本端没有显著变化）必须写出来"
    assert "不可判定" in note, "SQ-03 的实际状态（无法判定）必须写出来"


def test_attribution_fallback_prose_follows_the_actual_state():
    """归因型兜底分支：能落到那里的状态不止一种，措辞必须按状态生成。

    兜底此前写死「但支撑侧证据不可得，无法确认主营业务本身是否在扩张」，
    可还有一种落法：收入端与毛利率端**都是支持态**，挡住结论的是利润归属那一项。
    那时这句话描述的事情一件也没发生——支撑侧完全可得，缺的也不是数据。
    该分支不在四个例题的覆盖范围内，因此这里直接对判定函数构造状态。
    """
    from app.engine.pipeline import _decide
    from app.schemas import ThesisType

    class _E:
        def __init__(self, verdict, value=None):
            self.verdict, self.value = verdict, value

    base = {
        "SQ-01": _E(Verdict.SUPPORT, 0.5),
        "SQ-02": _E(Verdict.SUPPORT, 2.0),
        "SQ-03": _E(Verdict.SUPPORT, 1.0),
        "SQ-06": _E(Verdict.REFUTE, 9.0),
    }
    verdict, statement, coverage = _decide(
        ThesisType.ATTRIBUTION, base, "某标的（000000.SZ）", "命题原文"
    )
    assert verdict is Verdict.UNVERIFIABLE
    assert "支撑侧证据不可得" not in statement
    assert "缺可用数据" not in coverage
    assert "归属上市公司股东" in statement

    blocked = dict(base, **{"SQ-01": _E(Verdict.UNVERIFIABLE), "SQ-06": _E(Verdict.SUPPORT, 1.0)})
    verdict2, statement2, _ = _decide(
        ThesisType.ATTRIBUTION, blocked, "某标的（000000.SZ）", "命题原文"
    )
    assert verdict2 is Verdict.UNVERIFIABLE
    assert "收入端" in statement2 and "缺可用数据" in statement2


@pytest.mark.parametrize("cid", ALL_CASES)
def test_latched_rows_announce_themselves_in_the_direction_column(offline_runs, cid):
    """标了 already_triggered 的行，方向栏必须自带「已触发」前缀。

    前端与接口都可能被单独消费：只靠一个布尔字段，任何一处漏读就会把
    「已经发生的事」原样展示成「将来可能发生的风险」。
    """
    tv = offline_runs[cid]
    for c in tv.conclusion.falsification_conditions:
        assert isinstance(c.already_triggered, bool)
        if c.already_triggered:
            assert c.direction.startswith("已触发"), (
                f"「{c.monitored_variable}」标了已触发，方向栏却仍写着「{c.direction}」"
            )


def test_rows_whose_threshold_is_already_crossed_are_flagged(offline_runs):
    """三处实测的越线行，一处是反例。

    这张表叫「反转条件表」，读者默认它讲的是**未来**可能发生的事。
    把已经发生的事写成待触发的风险，读者会据此认为结论还很稳——方向正好反了。
    """
    # ① 贵州茅台：归母净利润同比已经是 −1.95%，阈值是「由正转负」。
    row = _row(offline_runs["maotai_divergence"], "归母净利润同比增速")
    assert "-1.95%" in row.current_value
    assert row.already_triggered is True

    # ② 宁德时代传导型：成本率累计变动 −2.01pp，阈值是「绝对变动回到 3pp 以内」。
    row = _row(offline_runs["catl_transmission"], "营业成本率累计变动")
    assert "-2.01pp" in row.current_value
    assert row.already_triggered is True

    # ③ 反例：宁德时代归因型的费用率，降幅 −2.35pp 早已越过 1pp，
    #    但同期收入同比 +54.80%，合取条件里的「且收入未增长」没有满足，
    #    该条的实际判定是**支持**（规模效应）。单看降幅就标已触发是错的。
    tv = offline_runs["catl_attribution"]
    row = _row(tv, "期间费用率同比变动")
    assert "-2.35pp" in row.current_value
    assert row.already_triggered is False
    assert evidence_by_sq(tv, "SQ-01").value > 0
    assert evidence_by_sq(tv, "SQ-04").verdict is Verdict.SUPPORT

    # ④ 未越线的行不许误标：平安的收入同比 +15.01%，离「转负」很远。
    row = _row(offline_runs["pingan_divergence"], "营业收入同比增速")
    assert row.already_triggered is False


@pytest.mark.parametrize("cid", ALL_CASES)
def test_declared_window_matches_the_window_actually_used(offline_runs, cid):
    """拆解面板声明的观察窗，必须与证据卡真正算的那个窗口一致。

    实测的错误：三处模板窗口都写作「最近 8 个报告期」，而取数层固定取 12 期，
    于是同一个「累计变动」在拆解面板（按 8 期声明）与证据卡（按 12 期计算）里
    得到两个口径，读者无从判断哪个对。这类错不报异常，只是让两处各说各话。

    这里不比对字面量，而是比对**证据卡报告期一栏里的真实期数**与模板声明的数字。
    """
    import re

    from app.engine.collect import HISTORY_LIMIT

    tv = offline_runs[cid]
    for sq in tv.decomposition.sub_questions:
        # 只查那些声明了「最近 N 期」的窗口；「最新报告期」这类没有期数，不参与比对。
        m = re.search(r"最近\s*(\d+)\s*期", sq.time_window)
        if m:
            assert int(m.group(1)) == HISTORY_LIMIT, (
                f"{sq.id} 的窗口声明为「最近 {m.group(1)} 期」，"
                f"而取数层实际取 {HISTORY_LIMIT} 期——声明与实现必须同源，"
                f"因此这里应当引用 HISTORY_LIMIT 而不是另写一个字面量"
            )
        # 证据卡上的真实期数不得超过声明上限。
        hits = [e for e in tv.evidence if e.sub_question_id == sq.id]
        for ev in hits:
            m2 = re.search(r"最近\s*(\d+)\s*期", ev.provenance.report_period)
            if m2 and m:
                assert int(m2.group(1)) <= int(m.group(1)), (
                    f"{sq.id} 的窗口声明「最近 {m.group(1)} 期」，"
                    f"证据卡却用了 {m2.group(1)} 期——实际窗口不能超过声明的上限"
                )


# --------------------------------------------------------------------------
# 估值分位：说分位就必须同时说窗口
#
# 重建序列的长度由可用报告期数决定，实测 361 / 371 个交易日（约 1.5 年）。
# 写成「处于自身历史 X 分位」，读者读到的却是「上市以来的历史低位」——
# 而两例背离型命题的核心前提（「估值确已回落」）都靠这一句支撑。
# 分位本身没错，错的是没把「这是多长的历史」说在同一句话里。
# 窗口的事实来源只有一个：证据卡的 provenance.report_period。
# --------------------------------------------------------------------------


DIVERGENCE_CASES = [c for c in ALL_CASES if c.endswith("divergence")]


@pytest.mark.parametrize("cid", DIVERGENCE_CASES)
def test_percentile_claim_carries_its_own_window(offline_runs, cid):
    """证据卡上那句「X 分位」必须自带起止日期，且与溯源的报告期一致。"""
    tv = offline_runs[cid]
    ev = evidence_by_sq(tv, "SQ-01")
    win = ev.provenance.report_period

    assert "自有可重建区间" in ev.claim, f"{cid} 的分位 claim 没有说明是哪一段历史：{ev.claim}"
    assert win in ev.claim, f"{cid} 的 claim 里没有窗口 {win}：{ev.claim}"
    for banned in ("自身历史", "自上市以来"):
        assert banned not in ev.claim, f"{cid} 的分位 claim 仍在暗示「{banned}」：{ev.claim}"
    # 区间长度必须能被读者看出来。实测 1.5 年，与「上市以来」差着数量级。
    assert "年" in ev.reasoning and "交易日" in ev.reasoning


@pytest.mark.parametrize("cid", DIVERGENCE_CASES)
def test_conclusion_does_not_promise_more_history_than_it_has(offline_runs, cid):
    """结论正文提到分位时，同样不能把它说成「自身历史」而不给窗口。"""
    tv = offline_runs[cid]
    stmt = tv.conclusion.statement
    if "分位" not in stmt:
        pytest.skip("本例的结论不以分位为论据")
    assert "自有可重建区间" in stmt, f"{cid} 的结论把分位说成了别的历史范围：{stmt}"
    assert tv.conclusion.statement.count("自上市以来") in (0, 1)
    if "自上市以来" in stmt:
        # 允许出现，但只能是以「不是自上市以来」这种否定形式出现
        assert "不是「自上市以来」" in stmt, f"{cid} 的结论正用「自上市以来」修饰分位：{stmt}"


@pytest.mark.parametrize("cid", DIVERGENCE_CASES)
def test_valuation_chart_footer_window_comes_from_the_series(offline_runs, cid):
    """图表页脚也要带窗口，且窗口取自这条曲线自身的首尾点。

    页脚此前写「处于自身历史 X 分位」，而图上的横轴明明只有一年多。
    这里的断言落在数据上而不是文案上：首尾点必须与证据卡的窗口同年份区间，
    这样前端无论怎么拼接那句话，窗口都是真的。
    """
    tv = offline_runs[cid]
    v = tv.charts.valuation
    if v is None or not v.series:
        pytest.skip("本例没有估值图")
    first, last = v.series[0].label, v.series[-1].label
    win = evidence_by_sq(tv, "SQ-01").provenance.report_period
    assert win.startswith(first) and win.endswith(last), (
        f"{cid} 的窗口 {win} 与曲线的首尾点 {first} ~ {last} 不一致——"
        f"页脚要印的窗口必须取自曲线本身"
    )


# --------------------------------------------------------------------------
# 命题里写全了带后缀的代码
#
# 线上实测出过的一次 500：`我认为贵州茅台（600519.SH）的估值已经回落…` 直接 500，
# 去掉括号里那段代码的同一句话 200。根因是解析层不带后缀（见 test_parse.py 那一组）。
# 这里守的是全链路：写全了代码就该一路跑到结论，而且不许掺进「未能确定标的」的噪声。
# --------------------------------------------------------------------------

THSCODE_IN_TEXT = "我认为贵州茅台（600519.SH）的估值已经回落，但基本面并没有恶化"
BARE_CODE_IN_TEXT = "我认为 600887 的估值已经回落，但基本面并没有恶化"


def test_thesis_with_full_code_runs_end_to_end():
    from tests.conftest import run_offline

    tv = run_offline(THSCODE_IN_TEXT)
    assert tv.parsed.thscode == "600519.SH"
    assert tv.conclusion is not None, "写全了代码却没能形成结论"
    assert not [e for e in tv.errors if "未能确定标的" in e], (
        f"代码已经写全，不该再报「未能确定标的」：{tv.errors}"
    )
    assert tv.evidence, "写全了代码却一条证据都没有"


def test_bare_code_reports_a_graceful_failure_instead_of_raising():
    """只有裸代码时：不崩、不猜后缀、如实说明要写成什么样。"""
    from tests.conftest import run_offline

    tv = run_offline(BARE_CODE_IN_TEXT)
    assert tv.conclusion is None
    assert tv.parsed.thscode is None
    assert any("交易所后缀" in e for e in tv.errors), (
        f"失败必须说清下一步该怎么写：{tv.errors}"
    )

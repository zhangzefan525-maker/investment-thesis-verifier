"""澄清环节：回答必须真的改变下游。

题目要求的主链路是「拆解、**澄清并修订**投资命题」。产品早先只做到「问」：
澄清问题连同默认假设一起展示，用户没法回答，v2 用的永远是产品的默认假设，
而界面上却写着「可在界面上修改后重跑」—— 这句话当时没有任何东西兑现它。

这一层守三件事：

1. **不回答时行为不变**。四条例题的既定结论不能被这个功能改动 ——
   真正守住它的是 `test_default_path_frozen.py`（与检入仓库的基线逐字段比对）。
   本文件里那条只证明「不传 answers」与「传空字典」两种写法等价。
2. **回答了要看得见**。用户的原话要进 v2 的前提，不能悄悄换成默认假设。
3. **回答要落到子问题上**。只把用户的话抄进一段文案、判定照旧，那是装饰不是功能；
   每一条被声明过的回答都必须能在证据或结论上找到它的作用。
"""

from __future__ import annotations

import json

import pytest

from app.engine.parse import ANSWER_EFFECTS, CLARIFY, _lookup_effect
from app.engine.run import apply_answer_effects, run_verification
from app.providers.fixture import FixtureProvider
from app.schemas import ThesisType

MAOTAI = "我认为贵州茅台估值已经回落但基本面并没有恶化"
CATL_ATTR = "我认为宁德时代盈利改善来自主营业务"
CATL_TRANS = "我认为碳酸锂价格上涨会挤压宁德时代的利润空间"

# 与结论无关的运行标识：每次运行都不同，不能拿来做「输出是否相同」的比对
_VOLATILE = ("run_id", "created_at")


def _stable(result) -> str:
    d = result.model_dump(mode="json")
    for k in _VOLATILE:
        d.pop(k, None)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _run(raw, answers=None):
    return run_verification(raw, provider=FixtureProvider(), answers=answers)


@pytest.fixture
def maotai():
    return _run(MAOTAI)


# --------------------------------------------------------------------------
# 一、未回答路径必须逐字不变
# --------------------------------------------------------------------------


def test_omitting_answers_equals_passing_an_empty_dict(maotai):
    """「不传 answers」与「传空字典」必须完全等价。

    这条**只**证明这一点，别把它当默认路径的护栏用 —— 它拿同一份代码自己和自己比，
    永远发现不了「这次改动把默认路径改了」。那个不变量在
    `test_default_path_frozen.py`：与一份检入仓库的、冻结于改动之前的基线逐字段比对。
    上一次真正的漂移（背离型第 3 问的默认假设文案被顺手改掉）就是从这条测试底下溜过去的。
    """
    assert _stable(maotai) == _stable(_run(MAOTAI, answers={}))


def test_default_path_carries_no_journal(maotai):
    """一句都没回答时，回执必须是空的 —— 没有任何一条回答被执行过。"""
    assert maotai.answer_journal == []


def test_unanswered_clarification_carries_no_answer_field(maotai):
    """没回答时 answer 必须是 None，不能被填成默认假设的文字。

    answer 与 assumption 是两个不同的东西：一个是用户说的，一个是产品替用户定的。
    合并成一个字段，就再也分不清结论是按谁的口径算的。
    """
    for c in maotai.parsed.clarifications:
        assert c.answer is None
        assert c.assumption.startswith("用户未指定时")
        assert c.impact == ""


# --------------------------------------------------------------------------
# 二、回答了要看得见
# --------------------------------------------------------------------------


def test_answer_is_quoted_verbatim_in_v2(maotai):
    """用户的原话要进 v2 的可验证化前提，而不是被默认假设顶掉。"""
    q = maotai.parsed.clarifications[1].question
    r = _run(MAOTAI, answers={q: "仅收入与利润"})

    answered = r.parsed.clarifications[1]
    assert answered.answer == "仅收入与利润"
    # 前提里必须出现这句原话。截断、改写、或换成默认假设都算失败。
    assert "仅收入与利润" in r.parsed.v2.text
    assert "采用最严格的一档" not in r.parsed.v2.text, "默认假设仍在 v2 里，说明回答没被采纳"


def test_boundary_spanning_answer_is_not_truncated(maotai):
    """回答里带全角冒号时，v2 里的前提不能被从冒号处截成半句。

    早先的前提是从 assumption 文案里 `split("：", 1)[-1]` 截出来的，
    回答里只要有一个冒号，用户写的前半句就会静默消失。
    """
    q = maotai.parsed.clarifications[0].question
    custom = "与同业比：沪深300 成分股"
    r = _run(MAOTAI, answers={q: custom})
    assert custom in r.parsed.v2.text


def test_decision_context_says_who_supplied_the_premises(maotai):
    """v2 要说明前提是谁给的。用户答了却仍写「由本产品自动补全」，是把功劳记错了人。"""
    assert "由本产品在澄清环节自动补全" in maotai.parsed.v2.decision_context

    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    ctx = r.parsed.v2.decision_context
    assert "用户在澄清环节指定" in ctx
    assert "由本产品在澄清环节自动补全" not in ctx


# --------------------------------------------------------------------------
# 三、回答必须落到子问题上（而不是只改一段文案）
# --------------------------------------------------------------------------


def test_peer_reference_frame_forces_sq01_unverifiable(maotai):
    """参照系选「与同业比」时，SQ-01 必须改判为无法验证，而不是照旧算自身历史分位。

    这是本产品最容易被做砸的地方：用户要的是同业分位，产品只有自身历史序列。
    拿自身历史分位冒充同业分位，屏幕上照样能显示一个「17.4% 低分位」，
    不报错、不崩页，只是答案回答的不是用户问的问题。
    """
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    sq01 = next(e for e in r.evidence if e.sub_question_id == "SQ-01")

    assert sq01.verdict.value == "unverifiable"
    assert sq01.value is None, "改判为无法验证却还留着一个数值，等于两处自相矛盾"
    assert sq01.unverifiable is not None
    # 失败原因必须说清是「口径要的东西数据源没有」，不是「这次调用失败了」
    assert sq01.unverifiable.category.value == "data_not_exist"
    assert "同业" in sq01.unverifiable.failure_evidence

    # 结论里不能再出现「估值确已回落」这类没算出来的断言
    assert "估值确已回落" not in r.conclusion.statement
    assert "本轮无法验证" in r.conclusion.statement


def test_narrowing_to_revenue_and_profit_drops_the_cash_sub_question(maotai):
    """命题收窄为「仅收入与利润」时，现金含量那条要移出本轮，并显式声明。

    照跑不误更省事，但那会让结论去否定一个用户没有提出的命题：
    用户说「我只关心收入和利润」，产品答「你的盈利质量有问题，所以不支持」。
    移出必须留痕 —— 静默少一条子问题，读者会以为本来就只有 5 条。
    """
    q = maotai.parsed.clarifications[1].question
    r = _run(MAOTAI, answers={q: "仅收入与利润"})

    assert all(s.id != "SQ-05" for s in r.decomposition.sub_questions)
    assert all(e.sub_question_id != "SQ-05" for e in r.evidence)
    assert "cash_quality" in [l.value for l in r.decomposition.skipped_layers]
    assert "仅收入与利润" in r.decomposition.skipped_layer_reasons["cash_quality"]


def test_conclusion_prose_does_not_speak_for_a_dropped_sub_question(maotai):
    """结论措辞不得替一条被移出的子问题说话。

    合取式结论的文案早先写死了「收入、利润与盈利质量三项均未见恶化」。
    SQ-05 一旦被移出，这句话就变成对一条没跑过的证据的断言 —— 比没有结论更糟。
    """
    q = maotai.parsed.clarifications[1].question
    r = _run(MAOTAI, answers={q: "仅收入与利润"})
    assert "盈利质量" not in r.conclusion.statement
    # 计数口径也要跟着走，不能还写「基本面侧 3 条」
    assert "基本面侧 3 条" not in r.conclusion.coverage_note


def test_uncertain_restructuring_claim_lands_in_applicable_scope(maotai):
    """回答「不确定」时，那条假设必须作为一条边界声明落进结论。"""
    q = maotai.parsed.clarifications[2].question
    r = _run(MAOTAI, answers={q: "不确定"})

    assert len(r.conclusion.limitations) == len(maotai.conclusion.limitations) + 1
    assert "未经核实" in r.conclusion.limitations[-1]


def test_answering_is_recorded_in_the_journal_not_in_errors(maotai):
    """回答改动了什么，必须出现在用户能看到的回执里 —— 但**不能**记成失败。

    改判发生在后端，若只在返回值里体现，用户点了重跑却看不出哪里变了，
    只能怀疑按钮没生效。可这条回执此前被并进 `errors`，而 errors 的定义是
    「本次运行中发生的失败」，前端按「失败透明清单」渲染它：用户答一句问题，
    页面上的失败条数就 +1。回答生效不是失败，两件事必须分开。
    """
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    assert any("与同业比" in e and "SQ-01" in e for e in r.answer_journal)
    assert not any("与同业比" in e for e in r.errors)


def test_journal_says_whether_the_verdict_actually_moved():
    """回执说的是**实际发生**的动作，不是表里那个动作的名字。

    传导型把链条位置断言成「下游」时，SQ-01 在本轮取数下本来就是「无法验证」
    （主营构成取不到），撤掉再放回一张新卡并没有改变判定。写「改判为无法验证」，
    就是在用户点完按钮后唯一那句回执里，把「什么都没变」说成「变了」。
    反过来，背离型选「与同业比」确实把 SQ-01 从 support 打成了无法验证，那句「改判」才是真的。
    """
    base = _run(CATL_TRANS)
    before = next(e for e in base.evidence if e.sub_question_id == "SQ-01").verdict
    assert before.value == "unverifiable", "本例的 SQ-01 本应取不到数，前提变了要重写这条测试"

    q = base.parsed.clarifications[2].question
    r = _run(CATL_TRANS, answers={q: "下游"})
    line = next(e for e in r.answer_journal if "SQ-01" in e)
    assert "改判" not in line, f"判定没有变，回执却说改判了：{line}"
    assert "未变" in line

    m = _run(MAOTAI)
    q0 = m.parsed.clarifications[0].question
    assert next(e for e in m.evidence if e.sub_question_id == "SQ-01").verdict.value == "support"
    r2 = _run(MAOTAI, answers={q0: "与同业比"})
    assert any("改判为无法验证" in e for e in r2.answer_journal)


# --------------------------------------------------------------------------
# 四、回答表的完整性与自洽
# --------------------------------------------------------------------------


def test_every_option_of_every_question_is_registered():
    """每个类型的每个澄清问题、每个选项，都必须在 ANSWER_EFFECTS 里有登记。

    漏一个不会报错，只会让那个选项选下去什么都不发生 ——
    用户选了、产品没反应，和「产品根本没这个功能」在体验上是一样的。
    """
    missing = [
        (t.value, i, o)
        for t, rows in CLARIFY.items()
        for i, (_q, _w, opts, _d) in enumerate(rows)
        for o in opts
        if (t, i, o) not in ANSWER_EFFECTS
    ]
    assert not missing, f"这些选项没有登记影响：{missing}"


def test_registered_keys_point_at_real_questions_and_options():
    """反向：表里不许有指向不存在的问题或选项的键。"""
    for (t, idx, opt) in ANSWER_EFFECTS:
        rows = CLARIFY[t]
        assert 0 <= idx < len(rows), f"{t.value} 第 {idx} 个问题不存在"
        assert opt in rows[idx][2], f"{t.value} 第 {idx} 问没有选项「{opt}」"


def test_unverifiable_effects_name_a_sub_question_that_exists():
    """改判为无法验证的动作必须指向模板里真实存在的子问题 ID。

    ID 只在同一套模板内唯一（归因型的 SQ-03 与背离型的 SQ-03 是两条完全不同的子问题），
    所以这里按命题类型分别查，不能只查一个全局集合。
    """
    from app.templates.gold import TEMPLATES

    for (t, _idx, _opt), entry in ANSWER_EFFECTS.items():
        kind, target, _text = entry[:3]
        # 表里的值是 3–5 元组：第 4 项是「同时追加到结论适用边界」的那句话，
        # 只有既改判又要留声明的选项才写；第 5 项是「该口径下真正缺的那份数据」，
        # 只有会写进改判卡「需要什么数据」一栏的选项才写（见 parse.py 该表上方的说明）。
        assert 3 <= len(entry) <= 5, f"{t.value}/{_opt} 的效果元组长度是 {len(entry)}"
        if kind in {"unverifiable", "drop"}:
            assert target, f"{t.value} 的 {kind} 动作没有指定子问题"
            ids = {s.id for s in TEMPLATES[t].sub_questions}
            assert target in ids, f"{t.value} 的模板里没有 {target}"


def test_unknown_answer_is_declared_as_having_no_effect(maotai):
    """自定义回答（不在选项内）必须如实声明「不改变下游」，而不是假装改了。

    这是本产品最该守住的一条：做不到就说做不到。硬把自定义口径套到最近的
    一个预设口径上，用户会以为结论就是按他问的口径算的。
    """
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与我自己比"})

    c = r.parsed.clarifications[0]
    assert c.answer == "与我自己比"
    assert "不会" in c.impact and "改动" in c.impact
    # 判定确实没变
    base = {e.sub_question_id: e.verdict for e in maotai.evidence}
    now = {e.sub_question_id: e.verdict for e in r.evidence}
    assert base == now


def test_no_effect_answers_leave_evidence_untouched(maotai):
    """选了「与产品默认一致」的那些选项，证据必须一条都不变。"""
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与自身历史比"})

    base = {e.sub_question_id: (e.verdict, e.display_value) for e in maotai.evidence}
    now = {e.sub_question_id: (e.verdict, e.display_value) for e in r.evidence}
    assert base == now
    assert _stable(r.conclusion) == _stable(maotai.conclusion)


# --------------------------------------------------------------------------
# 五、其余两类命题
# --------------------------------------------------------------------------


def test_attribution_narrowed_caliber_forces_sq01_unverifiable():
    """归因型选「仅含成熟业务」时，营收同比这条必须改判 —— 分部收入取不到。

    这正是产品已声明的 MIX 跳过层的同一个缺口，因此理由必须一致，
    不能新编一个「接口暂时不可用」。
    """
    base = _run(CATL_ATTR)
    q = base.parsed.clarifications[2].question
    r = _run(CATL_ATTR, answers={q: "仅含成熟业务"})

    sq01 = next(e for e in r.evidence if e.sub_question_id == "SQ-01")
    assert sq01.verdict.value == "unverifiable"
    # 「需要什么」一栏必须落到具体缺口上。只写「数据缺失」等于没答，
    # 读者无从判断这是产品的能力边界还是这次调用刚好失败了。
    assert "分部" in sq01.unverifiable.what_is_needed
    assert "仅含成熟业务" in sq01.unverifiable.failure_evidence


def test_transmission_chain_position_is_marked_as_user_asserted():
    """传导型断言链条位置时必须说明「这是用户说的、产品核实不了」。"""
    base = _run(CATL_TRANS)
    q = base.parsed.clarifications[2].question
    r = _run(CATL_TRANS, answers={q: "下游"})

    sq01 = next(e for e in r.evidence if e.sub_question_id == "SQ-01")
    assert sq01.verdict.value == "unverifiable"
    assert "下游" in sq01.unverifiable.failure_evidence
    assert "用户" in sq01.unverifiable.failure_evidence


def test_answering_does_not_turn_a_transmission_thesis_into_a_verdict():
    """传导型无论怎么回答，结论都不能从「证据不足」变成方向性结论。

    澄清环节只允许收窄或改判，不允许把不可验证的命题「答成」可验证的 ——
    那会让用户以为多填几个选项就能得到结论。
    """
    base = _run(CATL_TRANS)
    for i, c in enumerate(base.parsed.clarifications):
        for opt in CLARIFY[ThesisType.TRANSMISSION][i][2]:
            r = _run(CATL_TRANS, answers={c.question: opt})
            assert r.conclusion.verdict.value == "unverifiable", (
                f"回答「{opt}」后结论变成了 {r.conclusion.verdict.value}"
            )


# --------------------------------------------------------------------------
# 六、执行层：不发明未登记的影响
# --------------------------------------------------------------------------


def test_apply_answer_effects_is_a_noop_without_answers(maotai):
    """直接调执行层：没有回答时返回的对象必须与输入是同一批数据。"""
    from app.engine.run import build_decomposition, execute_evidence
    from app.engine.collect import collect

    ctx = collect(FixtureProvider(), maotai.parsed.thscode, "600519", "贵州茅台")
    dec = build_decomposition(maotai.parsed)
    ev = execute_evidence(ctx, maotai.parsed)

    dec2, ev2, extra, journal = apply_answer_effects(maotai.parsed, dec, ev)
    assert extra == [] and journal == []
    assert [s.id for s in dec2.sub_questions] == [s.id for s in dec.sub_questions]
    assert [e.sub_question_id for e in ev2] == [e.sub_question_id for e in ev]


def test_lookup_effect_returns_none_kind_for_unregistered_input():
    """查不到就如实返回「无效果」，不许猜一个最接近的效果出来。"""
    eff = _lookup_effect(ThesisType.DIVERGENCE, 0, "与火星比")
    assert eff.kind == "none" and eff.target == ""
    assert "不会" in eff.text
    assert eff.limitation == "" and eff.what_is_needed == ""


# --------------------------------------------------------------------------
# 七、回答生效之后，屏幕上的每一句话都得跟着换
#
# 这一层来自一次对抗式审计：它逐条去核「界面上（或选项文案里）写着的事，
# 代码里有没有东西兑现」。下面每一条都是一处实测过的落空，形状完全一样 ——
# 判定换了，措辞没换；或者承诺写了，动作没写。
# --------------------------------------------------------------------------


def test_chart_backed_by_an_invalidated_sub_question_is_withheld():
    """依据被作废的图必须撤下，并说清为什么 —— 不能照旧画，也不能悄悄没了。

    图表与证据是两条代码路径：图只读取数结果（`build_charts(ctx)`），
    不知道澄清环节已经把 SQ-01 判成「口径不可得」。实测：回答「与同业比」后，
    结论区写着「估值侧落入无法验证、不给方向性判断」，
    而图表页脚照旧印着「当前 PE 23.47，处于自身历史 8.0 分位」——
    那个数正是用户刚刚拒绝的那个口径算出来的。
    """
    base = _run(MAOTAI)
    assert base.charts.valuation is not None
    assert base.charts.withheld == []

    q = base.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})

    assert r.charts.valuation is None, "SQ-01 已判为口径不可得，估值图却还在"
    assert len(r.charts.withheld) == 1
    w = r.charts.withheld[0]
    assert w.backs_sub_question == "SQ-01"
    assert "无法验证" in w.reason and "撤下" in w.reason
    # 撤下的只是这一张：其它图依据的是别的子问题，不许连坐
    assert r.charts.profit is not None
    assert r.charts.margin is not None


def test_other_answers_do_not_withhold_the_valuation_chart():
    """反向：不涉及 SQ-01 的回答不能把估值图误撤下来。"""
    base = _run(MAOTAI)
    q = base.parsed.clarifications[1].question  # 命题范围：仅收入与利润（去掉的是 SQ-05）
    r = _run(MAOTAI, answers={q: "仅收入与利润"})
    assert r.charts.withheld == []
    assert r.charts.valuation is not None


def test_invalidated_predicate_only_fires_on_clarification_cards():
    """判据本身：只有澄清环节改判的卡才算「被作废」，取数得来的卡不算。

    用「value is None」当判据会把样本不足、取数失败一并算进来 ——
    那些情形下序列本身就不存在或不完整，图要么没有、要么本来就画不全；
    要抓的是「序列好端端在 ctx 里，而用它的那条证据已经撤了」。
    """
    from app.engine.pipeline import _invalidated_by_clarification
    from app.schemas import (
        Confidence,
        Evidence,
        Provenance,
        UnverifiableCategory,
        UnverifiableDetail,
        Verdict,
    )

    def card(endpoint: str) -> Evidence:
        return Evidence(
            id="EV-SQ-01",
            sub_question_id="SQ-01",
            claim="x",
            value=None,
            display_value="—",
            provenance=Provenance(
                source="同花顺扶摇",
                endpoint=endpoint,
                report_period="—",
                caliber="—",
                unit="—",
            ),
            decision_rule_applied="—",
            threshold_applied="—",
            verdict=Verdict.UNVERIFIABLE,
            confidence=Confidence.LOW,
            reasoning="—",
            unverifiable=UnverifiableDetail(
                category=UnverifiableCategory.SOURCE_UNREACHABLE,
                what_is_needed="y",
                where_to_get="z",
                failure_evidence="w",
            ),
        )

    data_card = card("GET /api/a-share/prices/historical")
    clar_card = card("(clarification: 用户在界面上指定的口径，非接口调用)")
    assert _invalidated_by_clarification([data_card], "SQ-01") is False
    assert _invalidated_by_clarification([clar_card], "SQ-01") is True
    assert _invalidated_by_clarification([], "SQ-01") is False
    # 问的不是这条子问题时不许误伤
    assert _invalidated_by_clarification([clar_card], "SQ-02") is False


def test_conflict_counterparty_is_declared_and_ids_all_exist():
    """冲突里出现的证据 id 必须**张张真实存在**；不是卡的那一端要显式声明。

    实测踩到两种编 id 的写法，都在同一条冲突上：
      * `[e1.id, "SNAPSHOT-PE-TTM"]` —— 后者是官方快照里的一个**数值**，
        前端把它渲染成「涉及证据 SNAPSHOT-PE-TTM」，读者去卡列表里找不到；
      * `ids or ["EV-SQ-01"]` —— 兜底用的假 id，凑 schema 的长度下界。

    这一例（宁德时代套背离型模板）实测重建偏差 43.2%，正是会走到这条冲突的那一条。
    """
    raw = "我认为宁德时代估值已经回落但基本面并没有恶化"
    r = _run(raw)
    ids = {e.id for e in r.evidence}

    high_gap = [c for c in r.conclusion.conflicts if "相对偏差达" in c.nature]
    assert high_gap, "这一例本应触发「重建偏差超容差」的冲突；触发条件变了就要改这条测试"

    c = high_gap[0]
    for eid in c.evidence_ids:
        assert eid in ids, f"冲突引用了不存在的证据 {eid}"
    assert c.counterparty and "pe_ttm" in c.counterparty, "另一端是数值，必须在 counterparty 里说清楚"

    # 全部四例：任何一条冲突都不许出现查无此卡的 id
    for raw in (MAOTAI, CATL_ATTR, CATL_TRANS):
        rr = _run(raw)
        known = {e.id for e in rr.evidence}
        for cc in rr.conclusion.conflicts:
            assert all(i in known for i in cc.evidence_ids), f"{raw} 的冲突引用了不存在的证据"


def test_external_variable_answer_lands_in_the_conclusion_not_the_card():
    """外部变量四个选项承诺的是「写进结论的适用边界」，实现就必须是这条。

    早先四个选项都写着「会把『需要什么数据』写成用户真正关心的那一个」，
    而 limitation 这条路径只往 conclusions.limitations 追加一句话，
    从不触碰任何证据卡上的文字 —— 四次承诺，四次没发生。
    """
    base = _run(CATL_TRANS)
    q = base.parsed.clarifications[0].question
    base_lims = list(base.conclusion.limitations)
    before = {(e.sub_question_id, e.verdict, e.display_value) for e in base.evidence}

    for opt in ("原材料价格", "下游需求", "政策变化", "行业景气度"):
        r = _run(CATL_TRANS, answers={q: opt})
        added = [x for x in r.conclusion.limitations if x not in base_lims]
        assert len(added) == 1, f"回答「{opt}」后结论适用边界增加了 {len(added)} 条"
        assert opt in added[0]

        c = next(x for x in r.parsed.clarifications if x.question == q)
        assert "适用边界" in c.impact, "文案要说清它真的会做什么"
        assert "需要什么数据" not in c.impact, "不再承诺一次并不存在的改写"

        after = {(e.sub_question_id, e.verdict, e.display_value) for e in r.evidence}
        assert before == after


def test_chain_position_assertion_reaches_the_conclusion():
    """选项承诺「结论会声明：链条位置由用户断言、未经核实」，结论里就必须真有这句。

    实测：这句承诺此前只落在证据卡上（`what_is_needed` 直接引用了它），
    而结论对象里一个字都没有 —— `conclusion.limitations` 五条，无一提及链条位置；
    把回答换成上游/中游/下游，结论逐字相同。用户会拿着这句承诺去结论里找。
    """
    base = _run(CATL_TRANS)
    q = base.parsed.clarifications[2].question
    base_lims = list(base.conclusion.limitations)

    for opt in ("上游", "中游", "下游"):
        r = _run(CATL_TRANS, answers={q: opt})
        added = [x for x in r.conclusion.limitations if x not in base_lims]
        assert len(added) == 1, f"回答「{opt}」后结论里没有这条声明"
        assert opt in added[0]
        assert "用户" in added[0] and "未经核实" in added[0]
        # 结论正文不能反过来把位置说成已核实过的事实
        assert "未经核实" not in r.conclusion.statement or "用户" in r.conclusion.statement


def test_unverifiable_card_does_not_blame_the_data_source():
    """取数路径的缺口不能写成数据源的缺口。

    「与同业比」做不到，是因为本产品没有「逐只标的拉取再横向拼」这条路径；
    扶摇**有**历史K线与利润表。写死成「扶摇公开接口不提供该数据」，
    读者会去申请一个本来就有权限的接口，并以为换个数据源就能解决。
    """
    base = _run(MAOTAI)
    q = base.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    d = next(e for e in r.evidence if e.sub_question_id == "SQ-01").unverifiable

    assert "扶摇" not in d.where_to_get
    assert "不提供" not in d.where_to_get
    # 「需要什么数据」这一栏要答所问：说的是**缺哪份数据**，不是「为什么做不到」。
    # 此前它填的是整段影响说明，于是同一张卡上「需要什么数据」与「推理」两栏
    # 出现同一段话，而前者答非所问。
    assert "历史K线" in d.what_is_needed and "同业可比公司" in d.what_is_needed
    card = next(e for e in r.evidence if e.sub_question_id == "SQ-01")
    assert d.what_is_needed != card.reasoning
    assert d.what_is_needed not in card.reasoning

    # 「判定规则」那一栏此前填的是子问题的**问句**（`sq.text`）——
    # 卡片上于是出现一句长得像规则、实际是问题的文字。本轮没有套用任何规则，
    # 就如实这么写，而不是拿问句顶上去。
    assert card.decision_rule_applied.startswith("本轮未套用判定规则")
    assert "？" not in card.decision_rule_applied



# --------------------------------------------------------------------------
# 八、回答对不上问题时必须被看见
#
# 回答以**题面为键**，而题面由命题类型生成。用户改一句命题、或 AI 把类型判成
# 另一类，整组题面就换掉，上一轮的回答会一条也对不上。此时按默认假设继续是对的，
# 但必须说出来 —— 此前是静默丢弃：answers.get(q) 取不到就当没回答，errors 里没有痕迹，
# 而按钮旁写着「有 N 条待生效」。用户答了、产品没听见，却告诉他听见了。
# --------------------------------------------------------------------------


def test_answer_that_matches_no_question_is_reported(maotai):
    """对不上的回答要原样列出来，且不得因此改动任何下游。"""
    r = _run(MAOTAI, answers={"一个本轮并不存在的题面": "与同业比"})

    assert r.parsed.unmatched_answers == ["与同业比"]
    # 听不见就不能装作听见：判定与一句都没回答时完全一致
    assert [e.verdict for e in r.evidence] == [e.verdict for e in maotai.evidence]
    assert r.conclusion.statement == maotai.conclusion.statement
    assert r.answer_journal == []


def test_matched_answers_are_not_reported_as_unmatched(maotai):
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    assert r.parsed.unmatched_answers == []


def test_a_custom_free_text_answer_is_heard(maotai):
    """「自己写一句」也算听见了 —— 它不在选项里，但它对上了那道题。"""
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "按 2025 年报口径"})
    assert r.parsed.unmatched_answers == []
    assert r.parsed.clarifications[0].answer == "按 2025 年报口径"
    assert r.parsed.clarifications[0].effect_kind == "none"
    assert "不会" in r.parsed.clarifications[0].impact


def test_effect_kind_is_the_action_that_actually_ran():
    """前端只有 `impact` 这段自然语言可用时，对所有回答一律写「本轮按你的回答计算」——
    而只追加边界声明的那些回答**没有改变任何计算**。`effect_kind` 就是给前端的机器可读标签，
    它必须与表里登记的类型、以及实跑发生的事三者一致。"""
    probes = [
        (MAOTAI, 0, "与同业比", "unverifiable"),
        (MAOTAI, 1, "仅收入与利润", "drop"),
        (MAOTAI, 2, "不确定", "limitation"),
        (MAOTAI, 0, "与自身历史比", "none"),
        (CATL_ATTR, 2, "不确定", "limitation"),
        (CATL_TRANS, 2, "下游", "unverifiable"),
        (CATL_TRANS, 1, "一个季度内", "limitation"),
    ]
    for raw, idx, opt, want in probes:
        base = _run(raw)
        q = base.parsed.clarifications[idx].question
        r = _run(raw, answers={q: opt})
        c = r.parsed.clarifications[idx]

        assert c.effect_kind == want, f"{raw[:12]}…第{idx}问选「{opt}」标成了 {c.effect_kind}"
        assert c.impact, "生效的回答必须说明它改了什么"

        if want == "limitation":
            # 只加一条边界声明：判定不动，limitations 多一条
            assert [e.verdict for e in r.evidence] == [e.verdict for e in base.evidence]
            assert len(r.conclusion.limitations) == len(base.conclusion.limitations) + 1
        elif want == "none":
            assert r.conclusion.statement == base.conclusion.statement
            assert r.conclusion.limitations == base.conclusion.limitations
        elif want == "drop":
            gone = {s.id for s in base.decomposition.sub_questions} - {
                s.id for s in r.decomposition.sub_questions
            }
            assert gone, "声明为「移出范围」的回答没有让任何子问题离开本轮"
            # 正文不得替这条离开的子问题说话 —— 细节见下一节那条测试
            assert all(s not in r.conclusion.statement for s in gone)
        elif want == "unverifiable":
            assert any(
                e.sub_question_id == "SQ-01" and e.verdict.value == "unverifiable"
                for e in r.evidence
            )


def test_dropped_sub_question_is_not_spoken_for_in_the_conclusion():
    """被移出范围的子问题，结论正文里不能再替它说话。

    SQ-05 被移出后，「收入、利润**与盈利质量**三项均未见恶化」这句里
    就多出了一项没跑过的证据 —— 措辞必须按实际在场的子问题生成。
    """
    base = _run(MAOTAI)
    q = base.parsed.clarifications[1].question
    r = _run(MAOTAI, answers={q: "仅收入与利润"})

    assert "SQ-05" not in {s.id for s in r.decomposition.sub_questions}
    assert "盈利质量" not in r.conclusion.statement
    assert "三层" not in r.conclusion.coverage_note

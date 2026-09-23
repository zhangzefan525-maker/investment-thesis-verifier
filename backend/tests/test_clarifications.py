"""澄清环节：回答必须真的改变下游。

题目要求的主链路是「拆解、**澄清并修订**投资命题」。产品早先只做到「问」：
澄清问题连同默认假设一起展示，用户没法回答，v2 用的永远是产品的默认假设，
而界面上却写着「可在界面上修改后重跑」—— 这句话当时没有任何东西兑现它。

这一层守三件事：

1. **不回答时行为不变**。四条例题的既定结论不能被这个功能改动，
   所以「未回答」路径必须与引入该功能前逐字相同。
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


def test_no_answers_is_byte_identical_to_before(maotai):
    """不传 answers / 传空字典 / 四个例题：输出必须与既有行为逐字相同。

    这条是整个功能的护栏。四例的结论是交付物的一部分（视频、文档、README 都引用了它们），
    一个「顺手改进」把默认假设改掉，等于把例题的结论悄悄换掉。
    """
    assert _stable(maotai) == _stable(_run(MAOTAI, answers={}))


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


def test_answering_is_recorded_in_the_failure_transparency_list(maotai):
    """回答改动了什么，必须出现在用户能看到的清单里。

    改判发生在后端，若只在返回值里体现，用户点了重跑却看不出哪里变了，
    只能怀疑按钮没生效。
    """
    q = maotai.parsed.clarifications[0].question
    r = _run(MAOTAI, answers={q: "与同业比"})
    assert any("与同业比" in e and "SQ-01" in e for e in r.errors)


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

    for (t, _idx, _opt), (kind, target, _text) in ANSWER_EFFECTS.items():
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
    kind, target, text = _lookup_effect(ThesisType.DIVERGENCE, 0, "与火星比")
    assert kind == "none" and target == ""
    assert "不会" in text

"""解析层测试（第 2 层）。

覆盖三类命题的关键词判定、标的主体抽取、以及两处**已经踩过的中文场景坑**：
- 中文环境下 `\\b(\\d{6})\\b` 的边界不成立；
- 标的名称抽取把口语前缀当成了公司名的一部分。
这两处都留下回归测试，防止改回去。
"""

from __future__ import annotations

import re

import pytest

from app.engine.parse import (
    _CODE_RE,
    _detect_subject,
    _detect_type,
    _subject_candidates,
    parse_thesis,
)
from app.schemas import ThesisType

MAOTAI = "我认为贵州茅台估值已经回落但基本面并没有恶化"
CATL_TRANSMISSION = "我认为碳酸锂价格上涨会挤压宁德时代的利润空间"
CATL_ATTRIBUTION = "我认为宁德时代盈利改善来自主营业务"
CODE_WITH_PREFIX = "我觉得600519.SH估值偏低了"


# --------------------------------------------------------------------------
# 命题类型判定
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param(MAOTAI, ThesisType.DIVERGENCE, id="divergence-maotai"),
        pytest.param(CATL_ATTRIBUTION, ThesisType.ATTRIBUTION, id="attribution-catl"),
        pytest.param(CATL_TRANSMISSION, ThesisType.TRANSMISSION, id="transmission-catl"),
    ],
)
def test_detect_type_three_classes(text: str, expected: ThesisType):
    got, rationale = _detect_type(text)
    assert got is expected
    assert rationale, "类型判定必须给出依据，不能只返回一个枚举值"


def test_detect_type_transmission_keywords_cover_price_squeeze_wording():
    """回归：传导型词表曾漏掉「价格上涨/挤压/利润空间」，把传导型命题误判成归因型。

    这三张词表是规则引擎的全部依据，漏词 = 误判。所以直接对词表本身下断言。
    """
    from app.engine.parse import TYPE_KEYWORDS

    for word in ("价格上涨", "挤压", "利润空间"):
        assert word in TYPE_KEYWORDS[ThesisType.TRANSMISSION], f"传导型词表缺少「{word}」"
        assert word not in TYPE_KEYWORDS[ThesisType.ATTRIBUTION], (
            f"「{word}」不该出现在归因型词表里，否则会导致误判"
        )


def test_transmission_is_not_misjudged_as_attribution():
    """端到端确认：这句命题不能落到归因型。"""
    got, _ = _detect_type(CATL_TRANSMISSION)
    assert got is not ThesisType.ATTRIBUTION
    assert got is ThesisType.TRANSMISSION


def test_detect_type_falls_back_to_attribution_without_keywords():
    """无关键词命中时按默认规则归入归因型，且必须在依据里写明这是默认而非判定。"""
    got, rationale = _detect_type("我认为这家公司不错")
    assert got is ThesisType.ATTRIBUTION
    assert "默认" in rationale


# --------------------------------------------------------------------------
# 标的主体抽取
# --------------------------------------------------------------------------


def test_detect_subject_strips_colloquial_prefix():
    """回归：「我认为贵州茅台估值…」必须抽出「贵州茅台」。

    曾经的问题是把口语前缀连同公司名一起截断，抽出「我认为贵州茅」这种不存在的名字。
    """
    code, name = _detect_subject(MAOTAI)
    assert code is None
    assert name == "贵州茅台"
    assert "我认为" not in name
    assert name != "我认为贵州茅"


def test_detect_subject_prefers_explicit_code():
    code, name = _detect_subject(CODE_WITH_PREFIX)
    assert code == "600519"
    assert name is None


def test_detect_subject_extracts_catl_from_transmission_sentence():
    code, name = _detect_subject(CATL_TRANSMISSION)
    assert code is None
    assert name == "宁德时代"


def test_subject_candidates_contains_catl():
    """候选列表必须包含「宁德时代」，且排在最前。

    A 股简称以四字最常见，因此按长度降序排；「宁德时代」紧邻「的」字，
    是唯一正确的四字切法（三字会切成「德时代」、五字会切成「压宁德时代」）。
    """
    cands = _subject_candidates(CATL_TRANSMISSION)
    assert "宁德时代" in cands
    assert cands[0] == "宁德时代", f"候选首项应为最可信的四字简称，实际 {cands}"


def test_subject_candidates_returns_empty_when_no_de_char():
    """没有「的」字时不做猜测——宁可返回空，让上层如实报「未识别出候选」。"""
    assert _subject_candidates(MAOTAI) == []


def test_chinese_word_boundary_regression():
    """回归：中文环境下 `\\b(\\d{6})\\b` 匹配不到「觉得600519」里的代码。

    Python 的 `\\w` 在 str 模式下包含中文，「得」和「6」都是 \\w，
    两者之间不构成词边界，因此带 \\b 的正则整条匹配失败。
    现行实现改用 `(?<!\\d)(\\d{6})(?!\\d)`，只依赖数字边界。
    """
    assert re.search(r"\b(\d{6})\b", CODE_WITH_PREFIX) is None, (
        "如果这里能匹配上，说明上面的结论不成立，回归测试的前提需要重新核对"
    )
    m = _CODE_RE.search(CODE_WITH_PREFIX)
    assert m is not None
    assert m.group(1) == "600519"


@pytest.mark.parametrize(
    "text,expected_code",
    [
        pytest.param("我觉得600519.SH估值偏低了", "600519", id="chinese-prefix"),
        pytest.param("600519 估值已经回落", "600519", id="leading-code"),
        pytest.param("看看 600519.SH 的估值", "600519", id="embedded-code"),
    ],
)
def test_code_regex_matches_six_digit_code(text: str, expected_code: str):
    m = _CODE_RE.search(text)
    assert m is not None
    assert m.group(1) == expected_code


def test_code_regex_ignores_longer_digit_runs():
    """7 位以上的数字串不是股票代码，不能被截出 6 位。"""
    assert _CODE_RE.search("订单号 1234567") is None


# --------------------------------------------------------------------------
# parse_thesis 入口
# --------------------------------------------------------------------------


def test_parse_thesis_produces_complete_structure():
    parsed = parse_thesis(MAOTAI)
    assert parsed.thesis_type is ThesisType.DIVERGENCE
    assert parsed.name == "贵州茅台"
    assert parsed.ticker is None  # 只给了名称，没给代码
    assert parsed.thscode is None  # 未消歧前不给后缀，避免猜
    assert parsed.v1.text == MAOTAI
    assert parsed.v1.version == "v1"
    assert parsed.v2.version == "v2"
    assert MAOTAI.rstrip("。") in parsed.v2.text
    assert len(parsed.diffs) == 3, "v1→v2 的修订差异必须逐条列出"


def test_parse_thesis_clarifications_declare_default_assumption():
    """每个澄清问题都必须写明「用户不回答时产品采用什么假设」——暗含的假设等于没有假设。"""
    parsed = parse_thesis(MAOTAI)
    assert len(parsed.clarifications) == 3
    for c in parsed.clarifications:
        assert c.question
        assert c.why_it_matters, "必须说明这个问题不清楚会导致哪条子问题无法判定"
        assert c.assumption, "必须写明默认假设"
        assert c.options, "应给出可选项"


def test_parse_thesis_keeps_resolved_ticker_from_provider():
    """外部消歧结果优先于规则抽取——规则抽取只是兜底。"""
    parsed = parse_thesis(MAOTAI, resolved_name="贵州茅台", thscode="600519.SH")
    assert parsed.thscode == "600519.SH"
    assert parsed.name == "贵州茅台"


def test_parse_thesis_type_rationale_names_the_engine():
    parsed = parse_thesis(MAOTAI)
    assert "规则引擎" in parsed.type_rationale


# --------------------------------------------------------------------------
# 命题里写全了代码（带交易所后缀）
#
# 这一组是补一个线上真出过的 500：命题里写了 `600519.SH` 时，解析层认出了 6 位代码、
# 却没有把用户明明写出来的后缀带上，于是构造出 ticker 有值、thscode 为空的
# ParsedThesis，撞上「不允许猜后缀」的校验器抛 ValidationError，异常穿到 API 变成 500。
# 更刺眼的是：识别不出标的时，产品给出的提示正是「请写明带交易所后缀的代码」。
# --------------------------------------------------------------------------

THSCODE_IN_TEXT = "我认为贵州茅台（600519.SH）的估值已经回落，但基本面并没有恶化"
BARE_CODE_IN_TEXT = "我认为 600887 的估值已经回落，但基本面并没有恶化"


def test_detect_thscode_reads_the_suffix_the_user_wrote():
    from app.engine.parse import _detect_thscode

    assert _detect_thscode(THSCODE_IN_TEXT) == "600519.SH"
    assert _detect_thscode("我觉得600519.SH估值偏低了") == "600519.SH"
    assert _detect_thscode("看看 000001.sz 的估值") == "000001.SZ"
    assert _detect_thscode("832000．BJ 的估值") == "832000.BJ"  # 全角点


def test_detect_thscode_does_not_guess_a_bare_code():
    """只有 6 位数字时**不猜后缀**——猜错交易所比不认标的更糟。"""
    from app.engine.parse import _detect_thscode

    assert _detect_thscode(BARE_CODE_IN_TEXT) is None
    assert _detect_thscode("订单号 1234567 与 600519") is None


def test_parse_thesis_uses_the_thscode_written_in_the_text():
    parsed = parse_thesis(THSCODE_IN_TEXT)
    assert parsed.thscode == "600519.SH"
    assert parsed.ticker == "600519"


def test_parse_thesis_never_carries_ticker_without_thscode():
    """只有裸代码时不能构造出 `ticker` 有值而 `thscode` 为空的对象。

    修复前这里抛 `ValidationError`（也就是线上那个 500）。正确行为是**不认标的**，
    把「没能确定标的」留给 run.py 的消歧段如实报出。
    """
    parsed = parse_thesis(BARE_CODE_IN_TEXT)
    assert parsed.ticker is None
    assert parsed.thscode is None

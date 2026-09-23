"""契约层测试（第 1 层）。

本项目的产品主张是「把约束写进 schema，而不是写在 README 里靠自觉」。
这一层就验证这句话是否成立：不合规的证据/子问题/溯源对象必须**构造失败**，
而不是构造成功之后靠下游自觉检查。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    Confidence,
    Evidence,
    ParsedThesis,
    Provenance,
    SubQuestion,
    ThesisType,
    ThesisVersion,
    UnverifiableCategory,
    UnverifiableDetail,
    Verdict,
)

# --------------------------------------------------------------------------
# 构造器
# --------------------------------------------------------------------------


def make_provenance(**overrides) -> Provenance:
    data = dict(
        source="同花顺扶摇",
        endpoint="GET /api/a-share/valuations/snapshot",
        report_period="2026Q2",
        caliber="TTM",
        unit="倍",
    )
    data.update(overrides)
    return Provenance(**data)


def make_detail(**overrides) -> UnverifiableDetail:
    data = dict(
        category=UnverifiableCategory.DATA_NOT_EXIST,
        what_is_needed="需要分部收入才能确定产业链位置",
        where_to_get="年报「分部报告」附注（巨潮资讯）",
        failure_evidence="接口字段清单中无分部收入字段",
    )
    data.update(overrides)
    return UnverifiableDetail(**data)


def make_evidence(**overrides) -> Evidence:
    data = dict(
        id="EV-SQ-01",
        sub_question_id="SQ-01",
        claim="营业收入同比 +1.47%",
        value=1.47,
        display_value="+1.47%",
        provenance=make_provenance(),
        decision_rule_applied="同比 ≥ 0 → 未恶化",
        threshold_applied="≥ 0",
        verdict=Verdict.SUPPORT,
        confidence=Confidence.HIGH,
        reasoning="营收同比增长 1.47%，按规则判为未恶化。",
    )
    data.update(overrides)
    return Evidence(**data)


def make_sub_question(**overrides) -> SubQuestion:
    data = dict(
        id="SQ-01",
        text="公司收入端是否恶化？",
        metric="营业收入同比增速",
        data_source="扶摇 /api/a-share/financials/income-statements",
        time_window="最新报告期 vs 上一年同一 fiscal_period",
        decision_rule="同比 ≥ 0 → 未恶化；< 0 → 已恶化",
        threshold="≥ 0",
        expected_direction=Verdict.SUPPORT,
        rationale="收入是基本面的起点。",
    )
    data.update(overrides)
    return SubQuestion(**data)


# --------------------------------------------------------------------------
# Evidence 的三态与详情必须配对
# --------------------------------------------------------------------------


def test_valid_evidence_constructs():
    """对照组：合规证据能构造出来——否则下面的失败断言可能只是因为构造器写错了。"""
    ev = make_evidence()
    assert ev.verdict is Verdict.SUPPORT
    assert ev.unverifiable is None


def test_unverifiable_without_detail_is_rejected():
    """判为无法验证却不写「需要什么/何时何地可得/失败证据」，必须在构造时就失败。"""
    with pytest.raises(ValidationError) as exc:
        make_evidence(verdict=Verdict.UNVERIFIABLE, value=None, display_value="—")
    assert "三问详情" in str(exc.value)


def test_non_unverifiable_with_detail_is_rejected():
    """非无法验证的证据携带详情同样是错的——它会让「哪条真的没验证成」变得不可读。"""
    with pytest.raises(ValidationError) as exc:
        make_evidence(verdict=Verdict.SUPPORT, unverifiable=make_detail())
    assert "非无法验证" in str(exc.value)


def test_refute_with_detail_is_rejected():
    """三态里的另一态（反对）带详情也必须失败，不能只挡住支持态。"""
    with pytest.raises(ValidationError):
        make_evidence(verdict=Verdict.REFUTE, unverifiable=make_detail())


def test_unverifiable_with_detail_is_accepted():
    ev = make_evidence(
        verdict=Verdict.UNVERIFIABLE,
        value=None,
        display_value="—",
        confidence=Confidence.LOW,
        unverifiable=make_detail(),
    )
    assert ev.unverifiable is not None
    assert ev.unverifiable.category is UnverifiableCategory.DATA_NOT_EXIST


def test_unverifiable_detail_requires_all_three_answers():
    """三问缺任何一问（字段直接不传），详情对象本身就构造不出来。"""
    for field in ("what_is_needed", "where_to_get", "failure_evidence"):
        data = dict(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            what_is_needed="需要什么",
            where_to_get="从哪拿",
            failure_evidence="失败证据",
        )
        del data[field]
        with pytest.raises(ValidationError):
            UnverifiableDetail(**data)


@pytest.mark.parametrize("field", ("what_is_needed", "where_to_get", "failure_evidence"))
@pytest.mark.parametrize(
    "value",
    [pytest.param(v, id=repr(v)) for v in ("", "   ", "-", "—", "N/A", "待定", "无", "暂无")],
)
def test_unverifiable_detail_rejects_blank_answers(field, value):
    """三问必须真的写出来。空串与占位符都等同于没答——「有字段」和「答了」是两回事。"""
    with pytest.raises(ValidationError):
        make_detail(**{field: value})


# --------------------------------------------------------------------------
# SubQuestion 五字段
# --------------------------------------------------------------------------

FIVE_FIELDS = ("metric", "data_source", "time_window", "decision_rule", "threshold")
PLACEHOLDERS = ["", "   ", "-", "N/A", "待定", "无"]


def test_valid_sub_question_constructs():
    sq = make_sub_question()
    assert sq.expected_direction is Verdict.SUPPORT


@pytest.mark.parametrize("field", FIVE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [pytest.param(p, id=repr(p)) for p in PLACEHOLDERS],
)
def test_sub_question_rejects_blank_or_placeholder(field: str, value: str):
    """五字段任一为空或填占位符（含 "N/A"/"待定"/"无"/"-"），都必须构造失败。

    这类值的危害是「看起来填了」——子问题会被当成可验证的带进拆解结果，
    实际运行时才发现取不到数。所以必须在类型层挡掉。
    """
    with pytest.raises(ValidationError):
        make_sub_question(**{field: value})


def test_sub_question_rejects_missing_field():
    with pytest.raises(ValidationError):
        SubQuestion(
            id="SQ-02",
            text="公司利润端是否恶化？",
            metric="归母净利润同比增速",
            # data_source 缺失
            time_window="最新报告期 vs 上年同期",
            decision_rule="同比 ≥ 0 → 未恶化",
            threshold="≥ 0",
            expected_direction=Verdict.SUPPORT,
            rationale="利润是基本面最直接的度量。",
        )


# --------------------------------------------------------------------------
# Provenance 五个溯源字段
# --------------------------------------------------------------------------

TRACE_FIELDS = ("source", "endpoint", "report_period", "caliber", "unit")


@pytest.mark.parametrize("field", TRACE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")],
)
def test_provenance_rejects_blank_trace_fields(field: str, value: str):
    """每个数字都必须能回到原始返回，所以来源/接口/报告期/口径/单位一个都不能空。"""
    with pytest.raises(ValidationError) as exc:
        make_provenance(**{field: value})
    assert "溯源字段不可为空" in str(exc.value)


def test_provenance_rejects_missing_field():
    with pytest.raises(ValidationError):
        Provenance(source="同花顺扶摇", report_period="2026Q2", caliber="TTM", unit="倍")


def test_provenance_defaults_carry_fetch_time_and_raw():
    """溯源还要留下抓取时间与原始片段——没有这两样，数字无法复核。"""
    p = make_provenance()
    assert p.fetched_at is not None
    assert isinstance(p.raw, dict)
    assert isinstance(p.request_params, dict)


# --------------------------------------------------------------------------
# ParsedThesis：给了 ticker 就必须给完整 thscode
# --------------------------------------------------------------------------


def _version(version: str) -> ThesisVersion:
    return ThesisVersion(version=version, text="命题", horizon="未来 2 个季度")


def test_parsed_thesis_rejects_ticker_without_thscode():
    """「标的代码必须标注」：只给六位代码、不给交易所后缀，构造即失败（不许猜后缀）。"""
    with pytest.raises(ValidationError) as exc:
        ParsedThesis(
            raw_text="我认为贵州茅台估值已经回落但基本面并没有恶化",
            thesis_type=ThesisType.DIVERGENCE,
            type_rationale="命中关键词",
            ticker="600519",
            thscode=None,
            name="贵州茅台",
            v1=_version("v1"),
            v2=_version("v2"),
        )
    assert "thscode" in str(exc.value)


def test_parsed_thesis_accepts_ticker_with_thscode():
    parsed = ParsedThesis(
        raw_text="我认为贵州茅台估值已经回落但基本面并没有恶化",
        thesis_type=ThesisType.DIVERGENCE,
        type_rationale="命中关键词「没有恶化」",
        ticker="600519",
        thscode="600519.SH",
        name="贵州茅台",
        v1=_version("v1"),
        v2=_version("v2"),
    )
    assert parsed.thscode == "600519.SH"


def test_parsed_thesis_allows_no_ticker_at_all():
    """识别不出标的时允许 ticker/thscode 都是 None——由上层如实报「未能确定标的」。"""
    parsed = ParsedThesis(
        raw_text="我认为这家公司不错",
        thesis_type=ThesisType.ATTRIBUTION,
        type_rationale="未命中关键词，按默认规则归入归因型",
        v1=_version("v1"),
        v2=_version("v2"),
    )
    assert parsed.ticker is None
    assert parsed.thscode is None

"""主链路编排入口。

    命题解析 → 模板装配 → 取数 → 证据判定 → 冲突检测 → 结论聚合 → 反转条件

这条链路上任何一环失败，都必须在 `ThesisVerification.errors` 里留下痕迹。
没有「静默跳过」这个分支。
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..providers.base import DataProvider, Err
from ..providers.fuyao import FuyaoClient, credential_status
from ..schemas import (
    Confidence,
    DecompositionLayer,
    DecompositionResult,
    Evidence,
    ParsedThesis,
    Provenance,
    SubQuestion,
    ThesisType,
    ThesisVerification,
    UnableToDecompose,
    UnverifiableCategory,
    UnverifiableDetail,
    Verdict,
)
from ..templates.gold import TEMPLATES
from .collect import collect
from .executors import (
    ATTRIBUTION_EXECUTORS,
    DIVERGENCE_EXECUTORS,
    TRANSMISSION_EXECUTORS,
    Ctx,
)
from .parse import _detect_subject, _subject_candidates, parse_thesis
from .pipeline import AggregateInput, aggregate, build_charts, detect_conflicts

EXECUTOR_SETS = {
    ThesisType.DIVERGENCE: DIVERGENCE_EXECUTORS,
    ThesisType.ATTRIBUTION: ATTRIBUTION_EXECUTORS,
    ThesisType.TRANSMISSION: TRANSMISSION_EXECUTORS,
}


def make_provider(prefer_live: bool = True) -> tuple[DataProvider, str, str]:
    """选择数据源。返回 (provider, mode, 说明)。

    扶摇凭据可用 → 实时模式；否则退回冻结快照。两种模式的差异会在 UI 上明示，
    不会让用户误以为在看实时数据。
    """
    status = credential_status()
    if prefer_live and status["present"]:
        client = FuyaoClient()
        return (
            client,
            "live",
            f"实时模式：数据直接来自同花顺扶摇接口。凭据来源：{status['origin']}。",
        )

    from ..providers.fixture import FixtureProvider

    fixtures = FixtureProvider()
    avail = fixtures.available()
    if avail:
        n = sum(len(v) for v in avail.values())
        return (
            fixtures,
            "fixture",
            f"冻结快照模式：未检测到扶摇凭据（{status['origin']}），"
            f"当前使用已捕获的 {len(avail)} 个标的共 {n} 份真实返回快照。"
            f"快照来自对扶摇接口的真实调用，非实时数据，数值不会随行情变化。",
        )
    return (
        fixtures,
        "fixture",
        f"冻结快照模式：未检测到扶摇凭据（{status['origin']}），且本地没有任何已捕获的快照。"
        f"此状态下所有子问题都会返回「无法验证」，并附带具体原因与补齐路径——"
        f"这是刻意行为：缺数据时如实报告，不用模拟数据顶替。",
    )


def resolve_thscode(provider: DataProvider, ticker: Optional[str], name: Optional[str]) -> tuple[
    Optional[str], Optional[str], Optional[str], list[str]
]:
    """把用户给的代码或名称消歧成完整 thscode。返回 (thscode, ticker, name, errors)。"""
    errors: list[str] = []
    query = ticker or name
    if not query:
        return None, ticker, name, ["命题中未能识别出标的名称或代码"]

    res = provider.search_ticker(query)
    if isinstance(res, Err):
        errors.append(f"标的消歧失败：{res.detail.failure_evidence}")
        return None, ticker, name, errors

    items = res.value if isinstance(res.value, list) else []
    if not items:
        errors.append(f"标的检索返回空：{query!r} 未匹配到任何 A 股标的")
        return None, ticker, name, errors

    # 优先取股票，其次取第一个
    pick = next((i for i in items if i.get("asset_type") == "a-share"), items[0])
    thscode = pick.get("thscode")
    if not thscode:
        errors.append(f"标的检索命中 {len(items)} 条记录但均无 thscode 字段")
        return None, ticker, name, errors
    if len(items) > 1:
        errors.append(
            f"标的检索命中 {len(items)} 条记录，已选用首条股票 {thscode}（{pick.get('name')}）；"
            f"其余 {len(items) - 1} 条未使用"
        )
    return thscode, pick.get("ticker") or ticker, pick.get("name") or name, errors


def build_decomposition(parsed: ParsedThesis) -> DecompositionResult:
    """装配子问题。模板是手工写死的，这里只做实例化——不发明新的子问题。"""
    tmpl = TEMPLATES[parsed.thesis_type]
    subs: list[SubQuestion] = []
    unable: list[UnableToDecompose] = []

    for sq in tmpl.sub_questions:
        try:
            subs.append(SubQuestion(**sq.model_dump()))
        except ValueError as exc:  # 五字段校验失败
            unable.append(
                UnableToDecompose(
                    attempted_question=sq.text,
                    missing_field="五字段之一",
                    reason=str(exc),
                    detail=UnverifiableDetail(
                        category=UnverifiableCategory.CALIBER_MISMATCH,
                        what_is_needed="补齐子问题的五个必填字段",
                        where_to_get="模板维护侧（templates/gold.py）",
                        failure_evidence=f"模板 {sq.id} 未通过五字段校验：{exc}",
                    ),
                )
            )

    # 显式声明本次未能覆盖的分解层。扶摇公开接口不含产销量/单价/分部/外币，
    # 因此这四个层在任何命题类型下都做不了——必须写出来，不能默认跳过。
    unsupported = [
        DecompositionLayer.VOLUME,
        DecompositionLayer.RATE,
        DecompositionLayer.MIX,
        DecompositionLayer.FX,
    ]
    skipped = [l for l in unsupported if l not in tmpl.layer_coverage]
    reasons = {
        DecompositionLayer.VOLUME: "扶摇公开接口不提供产销量数据，收入中的「量」无法分离",
        DecompositionLayer.RATE: "扶摇公开接口不提供分产品单价，收入中的「价」无法分离",
        DecompositionLayer.MIX: "扶摇公开接口不提供分部收入，业务结构变化的影响无法拆解",
        DecompositionLayer.FX: "扶摇公开接口不提供外币敞口与汇兑损益明细，汇率影响无法拆解",
    }

    return DecompositionResult(
        sub_questions=subs,
        unable_to_decompose=unable,
        skipped_layers=skipped,
        skipped_layer_reasons={l.value: reasons[l] for l in skipped},
        generated_by="hybrid",
    )


def execute_evidence(ctx: Ctx, parsed: ParsedThesis) -> list[Evidence]:
    execs = EXECUTOR_SETS[parsed.thesis_type]
    out: list[Evidence] = []
    for sq in TEMPLATES[parsed.thesis_type].sub_questions:
        fn = execs.get(sq.id)
        if fn is None:
            continue
        try:
            out.append(fn(ctx))
        except Exception as exc:  # 执行器自身出错也必须留痕
            ctx.run_errors.append(f"[{sq.id}] 执行器异常：{type(exc).__name__}: {exc}")
            out.append(_crash_evidence(ctx, sq.id, exc))
    return out


def _crash_evidence(ctx: Ctx, sq_id: str, exc: Exception) -> Evidence:
    return Evidence(
        id=f"EV-{sq_id}",
        sub_question_id=sq_id,
        claim="该子问题在执行过程中发生异常，未能得出结论",
        value=None,
        display_value="执行异常",
        provenance=Provenance(
            source="本产品执行器",
            endpoint=f"(executor crash: {sq_id})",
            report_period="—",
            caliber="—",
            unit="—",
            raw={"exception": f"{type(exc).__name__}: {exc}"},
        ),
        decision_rule_applied="—",
        threshold_applied="—",
        verdict=Verdict.UNVERIFIABLE,
        confidence=Confidence.LOW,
        reasoning=f"执行器抛出异常：{type(exc).__name__}: {exc}。按约束如实上报，不吞掉。",
        unverifiable=UnverifiableDetail(
            category=UnverifiableCategory.SOURCE_UNREACHABLE,
            what_is_needed="修复该子问题执行器的异常",
            where_to_get="backend/app/engine/executors.py",
            failure_evidence=f"{type(exc).__name__}: {exc}",
        ),
    )


def run_verification(
    raw_text: str,
    ticker: Optional[str] = None,
    name: Optional[str] = None,
    thscode: Optional[str] = None,
    provider: Optional[DataProvider] = None,
    data_mode: Optional[str] = None,
    data_mode_note: Optional[str] = None,
) -> ThesisVerification:
    """跑一次完整的命题验证。"""
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"

    if provider is None:
        provider, data_mode, data_mode_note = make_provider()
    data_mode = data_mode or "live"
    data_mode_note = data_mode_note or ""

    errors: list[str] = []

    # 1) 标的消歧
    # 三种入口都要能走通：给了 thscode / 给了代码或名称 / 什么都没给（只有一句自然语言命题）。
    # 最后一种从前端来，最常见——先从命题里抽主体，再交给检索接口消歧。
    # 即便已给出 thscode，也补一次名称：缺 name 会让解析器退回关键词抽取。
    if not thscode or not name:
        queries: list[Optional[str]] = []
        if ticker:
            queries.append(ticker)
        if name:
            queries.append(name)
        if thscode:
            queries.append(thscode)
        # 都没有时，穷举候选并**逐个用检索接口验证**。
        # 切词不可靠，但「这个名字在 A 股标的名录里存不存在」是可靠的事实。
        # 两个来源都要取：`的` 字切分覆盖「挤压宁德时代的…」这类句式，
        # 句首切分覆盖「中国平安估值已经回落…」这类没有「的」的句式，二者互补。
        if not queries:
            queries = list(_subject_candidates(raw_text))
            _, head_name = _detect_subject(raw_text)
            if head_name and head_name not in queries:
                queries.append(head_name)
            queries = queries or [None]

        tried: list[str] = []
        for q in queries:
            if q is None:
                continue
            t, tk, nm, res_errors = resolve_thscode(provider, q, None)
            if t:
                thscode, ticker, name = thscode or t, ticker or tk, name or nm
                if len(tried) > 1:
                    errors.append(
                        f"标的名称由候选逐个检索确定：依次尝试 {'、'.join(tried)}，"
                        f"最终命中 {nm or t}（{t}）。"
                    )
                break
            tried.append(q)
        else:
            errors.append(
                f"未能确定标的的完整 thscode，取数与验证无法开始。"
                f"已尝试的候选：{'、'.join(tried) or '（命题中未识别出任何候选）'}。"
                f"请在命题中直接写明六位股票代码或带交易所后缀的代码（如 600519.SH）以避免歧义。"
            )
        if not thscode and not ticker and not name:
            _, name = _detect_subject(raw_text)

    # 2) 命题解析
    parsed = parse_thesis(raw_text, resolved_ticker=ticker, resolved_name=name, thscode=thscode)

    if not thscode:
        return ThesisVerification(
            run_id=run_id,
            parsed=parsed,
            decomposition=DecompositionResult(),
            evidence=[],
            conclusion=None,
            data_mode=data_mode,  # type: ignore[arg-type]
            data_mode_note=data_mode_note,
            errors=errors + ["未能确定标的的完整 thscode，取数与验证无法开始"],
        )

    # 3) 取数
    ctx = collect(provider, thscode, ticker or "", name or "")
    errors.extend(ctx.run_errors)

    # 4) 拆解
    decomposition = build_decomposition(parsed)

    # 5) 取证与判定
    evidence = execute_evidence(ctx, parsed)
    # 执行器可能追加新的失败记录（取数异常、执行器异常），必须一并透出——
    # 只收集 collect 阶段的错误会让执行期的失败静默消失
    errors.extend(x for x in ctx.run_errors if x not in errors)

    # 6) 冲突检测
    conflicts = detect_conflicts(ctx, evidence, parsed.thesis_type)

    # 7) 结论聚合
    conclusion = aggregate(
        ctx,
        evidence,
        conflicts,
        AggregateInput(
            thesis_type=parsed.thesis_type,
            raw_text=raw_text,
            ticker=ctx.ticker,
            name=ctx.name,
        ),
    )

    return ThesisVerification(
        run_id=run_id,
        parsed=parsed,
        decomposition=decomposition,
        evidence=evidence,
        charts=build_charts(ctx),
        conclusion=conclusion,
        data_mode=data_mode,  # type: ignore[arg-type]
        data_mode_note=data_mode_note,
        errors=errors,
    )

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
    ClarificationQuestion,
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
from .parse import (
    _detect_subject,
    _detect_thscode,
    _lookup_effect,
    _subject_candidates,
    parse_thesis,
)
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

    # `generated_by` 必须说实话。这里此前写死为 "hybrid"，含义是
    # 「手工模板 + LLM 实例化」；但没有凭据、或 LLM 调用失败退回规则引擎时，
    # AI 一个字都没有参与，界面上却仍标着「hybrid」——
    # 把「AI 参与了」写在一个 AI 没参与的运行上，属于同一类「说了它没做的事」。
    # 子问题永远来自手工模板（没有任何代码让 AI 产生子问题内容），
    # 因此只有「类型判定与标的/时间窗抽取」这一步可能由 AI 完成。
    return DecompositionResult(
        sub_questions=subs,
        unable_to_decompose=unable,
        skipped_layers=skipped,
        skipped_layer_reasons={l.value: reasons[l] for l in skipped},
        generated_by="hybrid" if parsed.parsed_by == "llm" else "manual_gold_template",
    )


def _answer_unverifiable_evidence(
    parsed: ParsedThesis, sq_id: str, c: ClarificationQuestion, what_is_needed: str = ""
) -> Evidence:
    """按用户在澄清环节指定的口径，把一条子问题改判为「无法验证」。

    与「取数失败」必须分开：数据源没有任何问题，是用户问的那个口径本产品做不到。
    混为一谈的话，读者会以为重试一次、或者换个网络就有了。
    """
    tmpl = TEMPLATES[parsed.thesis_type]
    sq = next((s for s in tmpl.sub_questions if s.id == sq_id), None)
    return Evidence(
        id=f"EV-{sq_id}",
        sub_question_id=sq_id,
        claim=f"该子问题无法按用户在澄清环节指定的口径回答：{c.answer}",
        value=None,
        display_value="口径不可得",
        provenance=Provenance(
            source="本产品澄清环节",
            endpoint="(clarification: 用户在界面上指定的口径，非接口调用)",
            report_period="—",
            caliber=f"用户指定：{c.answer}",
            unit="—",
            raw={"question": c.question, "answer": c.answer},
        ),
        # 这一栏是「判定规则」，不是「子问题问什么」。
        # 此前填的是 `sq.text`（子问题的问句），卡片上于是出现一句像是规则、实际是问题的文字。
        # 本轮根本没有套用任何判据，就如实这么写。
        decision_rule_applied=(
            "本轮未套用判定规则 —— 该子问题没有按用户在澄清环节指定的口径取到数，"
            "因此不存在可比对的支持/反对阈值。"
        ),
        threshold_applied="—",
        verdict=Verdict.UNVERIFIABLE,
        # 高置信度：这不是「拿不准」，而是确定地做不到。给它低置信度会误导读者以为
        # 换个数据源重试就有结果。
        confidence=Confidence.HIGH,
        reasoning=c.impact,
        fact_or_logic="logic",
        unverifiable=UnverifiableDetail(
            category=UnverifiableCategory.DATA_NOT_EXIST,
            # 「需要什么数据」说的是**缺哪份数据**，不是「为什么做不到」。
            # 此前这里填的是整段影响说明，于是「需要什么数据」与「推理」两栏
            # 出现同一段话，而前者答非所问。缺什么，取自 ANSWER_EFFECTS 的第 5 项。
            what_is_needed=what_is_needed or c.impact,
            # 这里此前写死了「扶摇公开接口当前不提供该口径所需的数据」——
            # 而四个会走到这里的选项里，只有一个（分销/分部口径）真是数据源没有，
            # 另一个（背离型的「与同业比」）扶摇**有**历史K线和利润表，
            # 缺的是本产品「逐只标的拉取再横向拼」这条取数路径。
            # 把本产品的能力缺口写成数据源的缺口，读者会去申请一个本来就有权限的接口，
            # 而且会以为换个数据源就能解决。缺口在哪，就写在哪。
            where_to_get=(
                "取数范围不覆盖该口径（具体缺口见「需要什么数据」一栏，与本条回答同源）。"
                "接入该口径所需的数据源或取数路径后，重跑本命题即可覆盖该子问题；"
                "本轮不以近似口径顶替。"
            ),
            failure_evidence=(
                f"澄清环节用户把口径指定为「{c.answer}」，"
                f"而本轮取数范围不覆盖该口径；本子问题按约束改判为无法验证，未用近似口径顶替"
            ),
        ),
    )


def apply_answer_effects(
    parsed: ParsedThesis,
    decomposition: DecompositionResult,
    evidence: list[Evidence],
) -> tuple[DecompositionResult, list[Evidence], list[str], list[str]]:
    """把澄清回答落到子问题上，返回 (拆解, 证据, 追加的边界声明, 执行回执)。

    回答不落到下游就是装饰。这里只执行 `parse.ANSWER_EFFECTS` 里声明过的动作，
    不在执行期临时发明新的影响 —— 说明文字与实际行为同源于那张表，
    改一处不会只改到一半（早先的写法是文案里写「可修改后重跑」，而根本没有那个控件）。

    未回答时不做任何事，返回值与输入逐字相同：四条例题的既定行为不能被这个功能改动。

    返回的「执行回执」由调用方放进 `ThesisVerification.answer_journal`，
    **不能并进 errors** —— 回答生效不是失败，并进去会让界面上的「失败清单」条数凭空 +1。
    """
    extra_limitations: list[str] = []
    journal: list[str] = []

    subs = list(decomposition.sub_questions)
    ev = list(evidence)
    skipped = list(decomposition.skipped_layers)
    reasons = dict(decomposition.skipped_layer_reasons)

    for idx, c in enumerate(parsed.clarifications):
        if not c.answer:
            continue
        eff = _lookup_effect(parsed.thesis_type, idx, c.answer)
        target = eff.target

        if eff.kind == "limitation":
            extra_limitations.append(eff.text)
            journal.append(f"[{c.question}] → 追加适用边界：{c.answer}")
            continue

        # 改判类的选项也可以**同时**要求一条适用边界声明（表里的第 4 项）。
        # 只在 limitation 分支里追加是不够的：传导型把链条位置断言成「下游」时，
        # 撤销 SQ-01 只改了证据卡，而结论里那个「标的位置」是谁给的、核实过没有，
        # 只有写进 limitations 才说得清——选项文案承诺的正是这一句。
        if eff.limitation:
            extra_limitations.append(eff.limitation)
            journal.append(f"[{c.question}] → 追加适用边界：{c.answer}")

        if eff.kind == "unverifiable":
            # 回执必须说实话。传导型把链条位置断言成「下游」时，SQ-01 在本轮取数下
            # **本来就**是「无法验证」（主营构成取不到），撤掉再放回一张新卡并没有
            # 改变判定；写成「改判为无法验证」，就是在用户点完按钮后唯一那句回执里
            # 把「什么都没变」说成「变了」。
            before = next((e.verdict for e in ev if e.sub_question_id == target), None)
            ev = [e for e in ev if e.sub_question_id != target]
            ev.append(_answer_unverifiable_evidence(parsed, target, c, eff.what_is_needed))
            journal.append(
                f"[{c.question}] → {target} 的证据卡按你的口径重写"
                f"（判定仍是「无法验证」，未变）：{c.answer}"
                if before is Verdict.UNVERIFIABLE
                else f"[{c.question}] → {target} 改判为无法验证：{c.answer}"
            )
        elif eff.kind == "drop":
            layer = next(
                (
                    s.layer
                    for s in TEMPLATES[parsed.thesis_type].sub_questions
                    if s.id == target
                ),
                None,
            )
            subs = [s for s in subs if s.id != target]
            ev = [e for e in ev if e.sub_question_id != target]
            if layer is not None and layer not in skipped:
                skipped.append(layer)
                # 跳过原因要写明是「谁让它跳过的」。只写「本层做不到」，
                # 读者会以为这个缺口本来就存在 —— 而它是这次澄清回答新引入的。
                reasons[layer.value] = f"由澄清回答「{c.answer}」触发：{eff.text}"
            journal.append(f"[{c.question}] → {target} 移出本轮范围：{c.answer}")
        # kind == "none"：用户的选择与产品默认一致，下游不变。

    if not journal:
        return decomposition, evidence, [], []

    return (
        DecompositionResult(
            sub_questions=subs,
            unable_to_decompose=decomposition.unable_to_decompose,
            skipped_layers=skipped,
            skipped_layer_reasons=reasons,
            generated_by=decomposition.generated_by,
        ),
        ev,
        extra_limitations,
        journal,
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
    answers: Optional[dict[str, str]] = None,
) -> ThesisVerification:
    """跑一次完整的命题验证。

    `answers` 是用户对澄清问题的回答，键为问题原文。为空时全链路行为与引入该参数前逐字相同。
    """
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"

    if provider is None:
        provider, data_mode, data_mode_note = make_provider()
    data_mode = data_mode or "live"
    data_mode_note = data_mode_note or ""

    errors: list[str] = []

    # 1) 标的消歧
    # 三种入口都要能走通：给了 thscode / 给了代码或名称 / 什么都没给（只有一句自然语言命题）。
    # 命题里写全了带后缀的代码（600519.SH）时直接采信，不必再查一次检索接口。
    # 这不是「猜后缀」——后缀是用户自己写出来的。
    if not thscode:
        full = _detect_thscode(raw_text)
        if full:
            thscode = full
            ticker = ticker or full.split(".")[0]

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
            det_code, head_name = _detect_subject(raw_text)
            # 命题里只写了 6 位裸代码时，也把它交给检索接口去查一次——
            # 这是**查**（接口按代码返回带后缀的 thscode），不是**猜**后缀。
            if det_code and det_code not in queries:
                queries.insert(0, det_code)
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
            # 标的已经确定（命题里写全了代码）时，没查到名称不是失败——
            # 此前这里无条件报「未能确定标的」，于是「写全了代码」的命题
            # 会一边跑出结论、一边挂着一条自相矛盾的失败。
            if not thscode:
                errors.append(
                    f"未能确定标的的完整 thscode，取数与验证无法开始。"
                    f"已尝试的候选：{'、'.join(tried) or '（命题中未识别出任何候选）'}。"
                    f"请在命题中写明带交易所后缀的代码（如 600519.SH / 000001.SZ / 832000.BJ）"
                    f"以避免歧义。"
                )
        if not thscode and not ticker and not name:
            _, name = _detect_subject(raw_text)

    # 2) 命题解析
    parsed = parse_thesis(
        raw_text,
        resolved_ticker=ticker,
        resolved_name=name,
        thscode=thscode,
        answers=answers,
    )

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

    # 5.5) 澄清回答落地。必须发生在冲突检测与聚合之前 ——
    # 改判过的子问题要参与冲突检测，边界声明要进结论，晚了就等于没改。
    decomposition, evidence, extra_limitations, answer_journal = apply_answer_effects(
        parsed, decomposition, evidence
    )
    # 回执**不进 errors**。errors 的定义是「本次运行中发生的失败」，前端按
    # 「失败透明清单」渲染它：把「追加适用边界」塞进去，用户答一句问题就会看到
    # 失败条数 +1；而它恰恰是回答生效的凭据。放回它自己的字段。
    #
    # 这一条做过反向验证：把 `errors.extend(answer_journal)` 放回去、重启后端，
    # 浏览器里的「回答生效不再被算作失败」与「失败清单如实为空」当场变红。
    # 未做反向验证的护栏等于没有护栏——它可能恒真。

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
            extra_limitations=extra_limitations,
        ),
    )

    return ThesisVerification(
        run_id=run_id,
        parsed=parsed,
        decomposition=decomposition,
        evidence=evidence,
        # 图要拿到**改判后**的证据列表：澄清环节可能已经把某条子问题整体作废，
        # 而图是另一条代码路径（只读 ctx），不传进去它就不知道这件事。
        charts=build_charts(ctx, evidence),
        conclusion=conclusion,
        data_mode=data_mode,  # type: ignore[arg-type]
        data_mode_note=data_mode_note,
        errors=errors,
        answer_journal=answer_journal,
    )

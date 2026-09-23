"""M1 命题解析与 M2 命题修订。

## LLM 在这里做什么、不做什么

**做**：把用户的一句大白话，解析成 `{命题类型, 标的, 时间窗}`，并就模糊处提出澄清问题。

**不做**：不生成子问题、不生成判定规则、不产生任何数字。
子问题来自 `templates/gold.py` 的手工模板，数字来自扶摇接口并在代码里计算。

这样设计的原因是很实际的：LLM 自由拆解会拆出取不到数据的子问题，
然后为了「完成任务」而编造内容——这正好踩中「不虚构」这条硬约束。
把 LLM 的职责压缩到「分类 + 提取 + 提问」，它就不可能编造数字。

## 规则引擎是主路径，LLM 是增强

规则解析在无 LLM 凭据时也**完整可用**（这是刻意的设计，避免产品依赖外部额度的单点故障）。
配置了 ANTHROPIC_API_KEY 时，LLM 会接管分类与澄清问题的生成，规则引擎退为兜底。
无论走哪条路，`ParsedThesis` 的结构完全相同，UI 上看得到本次用的是哪条路。
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from ..schemas import (
    ClarificationQuestion,
    DecompositionLayer,
    ParsedThesis,
    ThesisDiff,
    ThesisType,
    ThesisVersion,
)

# --------------------------------------------------------------------------
# 规则引擎
# --------------------------------------------------------------------------

TYPE_KEYWORDS: dict[ThesisType, list[str]] = {
    ThesisType.DIVERGENCE: [
        "估值回落", "估值下行", "低估", "便宜", "错杀", "背离", "未恶化", "没有恶化",
        "跌下来", "回调", "股价下跌", "估值处于低位", "价值洼地",
    ],
    ThesisType.ATTRIBUTION: [
        "盈利改善", "盈利增长", "利润增长", "业绩改善", "主营", "主营业务", "增长质量",
        "内生增长", "利润来源", "业绩变好", "赚钱能力",
    ],
    ThesisType.TRANSMISSION: [
        "产业链", "传导", "上游", "下游", "供应链", "涨价", "提价", "利润分配",
        "行业格局", "成本传导", "需求变化", "影响利润",
        # 以下为实测补充：原词表漏掉了这批表述，导致
        # 「碳酸锂价格上涨会挤压宁德时代的利润空间」被误判为归因型
        "价格上涨", "价格下跌", "价格回落", "挤压", "压缩", "利润空间", "毛利空间",
        "成本压力", "议价能力", "定价权", "供需", "原材料", "涨价压力", "转嫁",
        "毛利率承压", "侵蚀",
    ],
}

# 注意：不能写 \b(\d{6})\b——Python 的 \w 在 str 模式下包含中文，
# 「觉得600519」里「得」和「6」之间不构成词边界，正则匹配不到。已实测踩过。
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_NAME_RE = re.compile(r"[「【]?([一-龥A-Za-z]{2,8})[」】]?")

HORIZON_PATTERNS = [
    (re.compile(r"(未来|接下来|下)\s*([一二三四五六七八九十\d]+)\s*个?(季度|月|年)"), None),
    (re.compile(r"(\d+)\s*个月"), None),
    (re.compile(r"(\d+)\s*年"), None),
]


def _detect_type(text: str) -> tuple[ThesisType, str]:
    scores: dict[ThesisType, list[str]] = {t: [] for t in ThesisType}
    for t, words in TYPE_KEYWORDS.items():
        for w in words:
            if w in text:
                scores[t].append(w)
    ranked = sorted(scores.items(), key=lambda kv: -len(kv[1]))
    best, hits = ranked[0]
    if not hits:
        # 无命中时默认走归因型——它是三类里对数据要求最温和的一类
        return (
            ThesisType.ATTRIBUTION,
            "未命中任何类型关键词，按默认规则归入归因型（三类中对数据要求最温和的一类）。"
            "该归类可由用户在界面上手动更正。",
        )
    return (
        best,
        f"命中关键词：{'、'.join(hits)}。三类命题中归入「{best.value}」，"
        f"依据是其关键词与该类型命题的结构特征一致。",
    )


def _detect_horizon(text: str) -> str:
    m = re.search(r"未来\s*([一二三四五六七八九十\d]+)\s*个?(季度|月|年)", text)
    if m:
        return f"未来 {m.group(1)} 个{m.group(2)}"
    m = re.search(r"(\d+)\s*(个月|年|个季度)", text)
    if m:
        return f"{m.group(1)} 个{m.group(2)}"
    return "未在命题中指定 —— 默认按最近 2 个报告期（约半年）评估"


# 命题里常见的口语前缀。这些词**作为前缀**出现时必须先剥掉，
# 否则「我认为贵州茅台估值…」会被整段截成公司名「我认为贵州茅」。
_SUBJECT_PREFIXES = (
    "我认为", "我觉得", "我看", "我判断", "我的判断是", "想请问", "请问",
    "分析一下", "看看", "帮我看看", "帮我",
)
# 出现即说明该片段不是公司名的词
_SUBJECT_NOISE = (
    "估值", "股价", "基本面", "业绩", "盈利", "利润", "收入", "风险", "逻辑", "命题",
    "价格", "上涨", "下跌", "回落", "成本", "产能", "需求", "供给", "行业", "市场",
    "上涨", "下跌", "会挤", "可能", "应该", "是否", "来自", "已经", "没有", "并未",
)


def _detect_subject(text: str) -> tuple[Optional[str], Optional[str]]:
    """从命题文本里抽标的。**只在没有外部消歧结果时才被调用**。

    这里的启发式本质上是不可靠的（中文公司名没有形态学边界），因此它只是一个
    兜底：正常路径下标的由 data provider 的检索接口消歧后传入。
    返回 None 也好过返回一个错误的名字——下游会如实报告「未能识别标的」。
    """
    m = _CODE_RE.search(text)
    if m:
        return m.group(1), None

    # 先剥掉口语前缀，再找公司名
    body = text
    for p in _SUBJECT_PREFIXES:
        if body.startswith(p):
            body = body[len(p):]
            break

    # 显式标注优先：「贵州茅台」/『贵州茅台』/【贵州茅台】
    m = re.search(r"[「『【]([一-龥A-Za-z]{2,8})[」』】]", body)
    if m:
        return None, m.group(1)

    cands = _subject_candidates(body)
    if cands:
        return None, cands[0]

    # 兜底：公司简称常紧贴谓述短语之前（「贵州茅台估值…」）。
    # 定位第一个「谓述起点」，取它前面那一小段——只扫全句会把
    # 「已经回落但基」这类描述片段当名字，那是错的（已实际发生过）。
    cut = len(body)
    for kw in _SUBJECT_NOISE + ("在", "是", "将", "已", "会"):
        i = body.find(kw)
        if i >= 2:
            cut = min(cut, i)
    head = re.findall(r"[一-龥A-Za-z]{2,6}", body[:cut])
    return (None, head[-1]) if head else (None, None)


def _subject_candidates(text: str) -> list[str]:
    """给出**按可信度排序**的标的候选，而不是一个答案。

    中文公司名没有形态学边界，纯靠正则不可能一次切准：
    「会挤压宁德时代的利润空间」里，紧邻「的」的四个字正好是「宁德时代」，
    但三个字就成了「德时代」，五个字又成了「压宁德时代」。
    任何单一长度的切法都会在某些句子上出错。

    因此这里不做「猜一个」，而是穷举主流的公司名长度（4/3/2 字，A 股简称
    四字最普遍、三字次之），按长度降序返回。调用方拿这个列表**逐个去数据源的
    检索接口验证**，命中即止——把「切词」这件不可靠的事，交给一个可靠的
    外部事实来裁决。全部落空时如实报告，而不是硬猜一个。
    """
    out: list[str] = []
    for m in re.finditer("的", text):
        i = m.start()
        for n in (4, 3, 2):
            if i - n < 0:
                continue
            cand = text[i - n:i]
            if not all("一" <= c <= "鿿" for c in cand):
                continue
            if any(nd in cand for nd in _SUBJECT_NOISE):
                continue
            if cand not in out:
                out.append(cand)
    return out


# --------------------------------------------------------------------------
# 澄清问题（规则生成，按类型固定）
# --------------------------------------------------------------------------

CLARIFY: dict[ThesisType, list[tuple[str, str, list[str], str]]] = {
    ThesisType.DIVERGENCE: [
        (
            "「估值回落」的参照系是什么？是与自身历史比、与同业比、还是与大盘比？",
            "参照系不同会改变「回落」的判定阈值。若不确定，本条子问题（SQ-01）"
            "计算出的分位无法解释为「低估」还是「正常」。",
            ["与自身历史比", "与同业比", "与大盘比"],
            "采用「与自身上市以来历史比」——这也是本产品唯一能完整取到数据的一种参照系",
        ),
        (
            "「基本面未恶化」具体指哪几个维度？收入、利润、还是包含盈利质量？",
            "只检验收入利润会漏掉「利润没变成现金」这类隐性恶化，"
            "导致 SQ-05 形同虚设。",
            ["仅收入与利润", "收入利润 + 盈利质量 + 现金流"],
            "采用最严格的一档：收入、利润、毛利率、现金流四项全部检验",
        ),
        (
            "给定的时间窗内是否发生过重大资产重组或主营变更？",
            "若发生，历史可比性被破坏，同比数据的口径不再一致，会在 SQ-03/SQ-04 上产生伪信号。",
            ["发生过", "未发生", "不确定"],
            "按「未发生」处理，但本产品会在结论的适用边界中声明该假设未经核实",
        ),
    ],
    ThesisType.ATTRIBUTION: [
        (
            "「盈利改善」指的是哪个口径的利润？归母净利润、扣非净利润、还是经营利润？",
            "不同口径下「是否来自主营业务」的答案可能相反——"
            "归母净利润增长但扣非净利润下滑，正是本命题最典型的反例。",
            ["归母净利润", "扣非净利润", "营业利润"],
            "以归母净利润为主口径，同时用扣非口径（SQ-03）做交叉检验",
        ),
        (
            "对比基期是哪一期？同比、环比、还是与三年前比？",
            "基期不同，增速完全不同，SQ-01/SQ-02 的阈值判定会随之改变。",
            ["上年同期", "上一季度", "三年前同期"],
            "采用上年同期（同 fiscal_period），这是唯一能保证口径一致的比法",
        ),
        (
            "评判「主营业务」时是否包含公司自己定义为「主营」的新业务？",
            "新业务在报表上可能确实计入营业收入，但尚未形成可持续的盈利能力，"
            "会影响 SQ-01 的解读。",
            ["包含", "仅含成熟业务", "不确定"],
            "按「包含」处理（按报表口径），并在结论中声明该假设",
        ),
    ],
    ThesisType.TRANSMISSION: [
        (
            "「外部变化」具体是什么？可否量化表述？",
            "这是传导型命题的根节点。若该变量本身无法被观测（如「行业景气度回暖」），"
            "整条链路在 SQ-05 就会被判为无法验证。",
            ["原材料价格", "下游需求", "政策变化", "行业景气度"],
            "不做默认假设 —— 本产品会直接检验该变量本身在本数据源中是否可观测",
        ),
        (
            "传导路径预期有多长？一个季度、半年、还是一年以上？",
            "传导时滞决定了「现在看不到效果」是否算证伪。",
            ["一个季度内", "半年内", "一年以上"],
            "采用「半年内」作为观察窗，即 2 个报告期",
        ),
        (
            "标的是链条的上游、中游还是下游？",
            "位置决定了成本变化对它是利空还是利多——上游涨价对上游是利好、对下游是利空。",
            ["上游", "中游", "下游", "不确定"],
            "不做默认假设 —— 本产品会先检验主营构成是否可得（SQ-01），"
            "若不可得则整条链路判为无法验证",
        ),
    ],
}


def _clarifications(ttype: ThesisType) -> list[ClarificationQuestion]:
    return [
        ClarificationQuestion(
            question=q,
            why_it_matters=why,
            options=opts,
            assumption=f"用户未指定时，本产品采用：{default}。该假设会随结论一并展示，"
                       f"并可在界面上修改后重跑。",
        )
        for q, why, opts, default in CLARIFY[ttype]
    ]


def _revise(
    ttype: ThesisType,
    raw: str,
    subject: str,
    horizon: str,
    clarifications: list[ClarificationQuestion],
) -> tuple[ThesisVersion, ThesisVersion, list[ThesisDiff]]:
    """构造 v1 → v2 的修订。v1 是用户原话，v2 是把默认假设显式写入后的可验证版本。"""
    v1 = ThesisVersion(
        version="v1",
        text=raw.strip(),
        horizon=horizon,
        decision_context="由用户原始输入直接记录，未做任何加工。",
    )

    assumptions = "；".join(c.assumption.split("：", 1)[-1].rstrip("。") for c in clarifications)
    tightened = (
        f"关于 {subject}，在「{horizon}」的时间窗内，"
        f"{raw.strip().rstrip('。')}。"
        f"（本命题的可验证化前提：{assumptions}）"
    )
    v2 = ThesisVersion(
        version="v2",
        text=tightened,
        horizon=horizon,
        decision_context=(
            f"命题类型判定为「{ttype.value}」。上述前提由本产品在澄清环节自动补全，"
            f"目的是把原命题中隐含的、无法证伪的部分显式化——"
            f"不明确参照系与口径，任何「验证」都只是自说自话。"
        ),
    )

    diffs = [
        ThesisDiff(
            field="参照系 / 对比基期",
            before="未指定",
            after="已明确（见 v2 前提）",
            reason="不指定参照系的「回落」「改善」无法证伪，必须补全才能进入子问题拆解。",
        ),
        ThesisDiff(
            field="时间窗",
            before="未指定" if "未在命题中指定" in horizon else horizon,
            after=horizon,
            reason="时间窗决定了用哪几期财务数据、以及反转条件表里的「下次披露时点」。",
        ),
        ThesisDiff(
            field="口径",
            before="未指定（归母/扣非/合并口径混杂）",
            after="已明确（见 v2 前提）",
            reason="同一句话在不同口径下可能得出相反结论，口径必须前置声明。",
        ),
    ]
    return v1, v2, diffs


# --------------------------------------------------------------------------
# LLM 增强（可选）
# --------------------------------------------------------------------------


def _llm_parse(raw: str, api_key: str) -> Optional[dict]:
    """调用 Claude 做分类与澄清。失败返回 None，由规则引擎接管。"""
    try:
        import httpx
    except ImportError:
        return None

    schema_hint = (
        '{"thesis_type": "divergence|attribution|transmission", '
        '"type_rationale": "为什么这样归类", '
        '"subject": "标的名称或六位代码", '
        '"horizon": "时间窗", '
        '"ambiguities": [{"question": "澄清问题", "why": "不清楚会怎样影响验证", '
        '"default_assumption": "未回答时的默认假设"}]}'
    )
    prompt = (
        "你是一个投资命题的形式化助手。你的唯一职责是把用户的一句话命题，"
        "转成结构化字段，并就其中无法证伪的模糊处提出澄清问题。\n\n"
        "严格约束：\n"
        "1. 绝对不要生成任何数字、财务数据或市场判断。\n"
        "2. 不要评价这个命题对不对，只做分类、提取与提问。\n"
        "3. 命题类型只能是 divergence（估值与基本面背离）/ attribution（盈利归因）"
        "/ transmission（产业链传导）三者之一。\n"
        "4. 只输出 JSON，不要任何解释文字。\n\n"
        f"统一 JSON 结构：{schema_hint}\n\n"
        f"用户命题：{raw}"
    )
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
                "max_tokens": 1200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=40.0,
        )
        resp.raise_for_status()
        body = resp.json()
        text = "".join(
            blk.get("text", "") for blk in body.get("content", []) if blk.get("type") == "text"
        )
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def parse_thesis(raw: str, resolved_ticker: Optional[str] = None,
                 resolved_name: Optional[str] = None,
                 thscode: Optional[str] = None) -> ParsedThesis:
    """解析命题。优先 LLM，失败或无凭据时退回规则引擎。"""
    code, nm = _detect_subject(raw)
    ticker = resolved_ticker or code
    name = resolved_name or nm

    ttype, rationale = _detect_type(raw)
    horizon = _detect_horizon(raw)
    clar = _clarifications(ttype)
    engine = "规则引擎"

    if llm_available():
        got = _llm_parse(raw, os.environ["ANTHROPIC_API_KEY"])
        if got:
            try:
                cand = ThesisType(got.get("thesis_type", ""))
                ttype = cand
                rationale = (
                    f"[Claude 解析] {got.get('type_rationale', '')} "
                    f"（规则引擎的独立判断为「{_detect_type(raw)[0].value}」，"
                    f"{'两者一致' if _detect_type(raw)[0] is cand else '两者不一致，已采用 Claude 的判断并在 UI 标注'}）"
                )
                if got.get("horizon"):
                    horizon = got["horizon"]
                if got.get("subject") and not (ticker or name):
                    s = str(got["subject"])
                    if s.isdigit() and len(s) == 6:
                        ticker = s
                    else:
                        name = s
                amb = got.get("ambiguities") or []
                if amb:
                    clar = [
                        ClarificationQuestion(
                            question=a.get("question", ""),
                            why_it_matters=a.get("why", ""),
                            options=[],
                            assumption=a.get("default_assumption", ""),
                        )
                        for a in amb
                        if a.get("question")
                    ] or clar
                engine = "Claude 解析（规则引擎已并行校验）"
            except (ValueError, TypeError):
                pass

    v1, v2, diffs = _revise(ttype, raw, name or ticker or "该标的", horizon, clar)

    return ParsedThesis(
        raw_text=raw,
        thesis_type=ttype,
        type_rationale=f"{rationale}（解析引擎：{engine}）",
        ticker=ticker,
        thscode=thscode,
        name=name,
        v1=v1,
        clarifications=clar,
        v2=v2,
        diffs=diffs,
    )

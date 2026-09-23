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
from typing import NamedTuple, Optional

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
# 写全了的代码：600519.SH / 000001.sz / 832000．BJ（全角点）
_THSCODE_RE = re.compile(r"(?<!\d)(\d{6})\s*[.．]\s*(SH|SZ|BJ)(?![A-Za-z0-9])", re.IGNORECASE)
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


def _detect_thscode(text: str) -> Optional[str]:
    """命题里自己写了交易所后缀时直接采信——**这不是「猜后缀」**。

    只有 6 位数字时返回 None：后缀一旦猜错，取的是一整套错标的的数据，
    而错误会一路走到结论里；不认标的只是少跑一次，代价小得多。
    这个区分是实测逼出来的（见 tests/test_parse.py 那一组）：
    此前 `_detect_subject` 认出了那 6 位数字却丢掉了用户明明写出来的 `.SH`，
    构造出 ticker 有值、thscode 为空的对象，撞上「不允许猜后缀」的校验器，
    把一个本该是「如实说做不到」的场景变成了 HTTP 500。
    """
    m = _THSCODE_RE.search(text)
    return f"{m.group(1)}.{m.group(2).upper()}" if m else None


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
            "按「未发生」处理。该假设会随 v2 前提一并披露，如实际发生过可在澄清环节改选后重跑",
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
            "按「包含」处理（按报表口径）。该假设会随 v2 前提一并披露，"
            "如口径需要调整可在澄清环节改选后重跑",
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


# --------------------------------------------------------------------------
# 澄清回答 → 下游影响
#
# 键是 (命题类型, 该类型下第几个澄清问题, 选项原文)，值是 (影响类型, 作用对象, 说明)。
#
# 这张表是**唯一**的描述来源：说明文字取自它，run.py 的改子问题逻辑也取自它。
# 早先说明是写死的字符串（答了也不会改任何东西），文案与行为各说各话——
# 产品明说「可在界面上修改后重跑」，而界面上根本没有那个控件。
#
# 影响类型只有四种，每种都对应一个真实动作：
#   unverifiable —— 该子问题在本数据源下无法按用户指定的口径重算，强制判为无法验证
#   drop         —— 该子问题被移出本轮检验范围，并在跳过层中显式声明
#   limitation   —— 不改判定，但给结论追加一条适用边界声明
#   none         —— 用户的选择与本产品默认一致，下游不变
#
# 值是 (影响类型, 作用对象, 说明文字[, 追加到结论适用边界的那一句[, 需要什么数据]])。
# 第 4 项可省略（省略即不追加），只有**同时**要改判又要留边界声明时才写。
# 它存在的理由：unverifiable 这条路径改的是证据卡，卡本身不进 conclusions.limitations，
# 而有的选项两边都要——例如传导型把链条位置断言成「下游」，既要撤掉 SQ-01，
# 又要在结论里写明这个位置是用户断言的、本产品没核实过。只有证据卡说、结论不说，
# 读者就不会知道结论里那个「标的位置」是谁给的。
#
# 第 5 项是**该口径下真正缺的那份数据**，写进改判卡的「需要什么数据」一栏。
# 此前那一栏填的是整段说明文字（说明讲的是「为什么做不到」，不是「缺什么」），
# 于是卡片上「需要什么数据」与「推理」两栏出现同一段话，而前者答非所问。
# --------------------------------------------------------------------------

ANSWER_EFFECTS: dict[tuple[ThesisType, int, str], tuple[str, ...]] = {
    # --- 背离型 ---
    (ThesisType.DIVERGENCE, 0, "与同业比"): (
        "unverifiable",
        "SQ-01",
        "SQ-01 的参照系被指定为「同业」，而本轮取数只覆盖用户指定的这一只标的："
        "重建同业各家的 PE 序列需要逐只调历史K线与利润表，本产品当前没有这条取数路径，"
        "因此该子问题判为无法验证，而不是换成「与自身历史比」硬算一个数给用户。",
        "",
        "同业可比公司清单，以及这些公司同期的历史K线与利润表"
        "（扶摇逐只有数，缺的是本产品「逐只拉取再横向拼成一条同业 PE 序列」这条取数路径）",
    ),
    (ThesisType.DIVERGENCE, 0, "与大盘比"): (
        "unverifiable",
        "SQ-01",
        "SQ-01 的参照系被指定为「大盘」。指数的 PE 分位需要指数自身的盈利序列，"
        "本产品的取数范围只包含标的个股的K线与合并利润表，没有指数盈利数据，"
        "因此该子问题判为无法验证。",
        "",
        "指数自身的盈利序列（用于算出指数 PE 及其分位，本产品当前只取个股的利润表）",
    ),
    (ThesisType.DIVERGENCE, 0, "与自身历史比"): (
        "none",
        "",
        "与产品默认参照系一致，下游取数与判定不变。",
    ),
    (ThesisType.DIVERGENCE, 1, "仅收入与利润"): (
        "drop",
        "SQ-05",
        "命题范围被收窄为收入与利润两项，SQ-05（盈利质量与现金流）被移出本轮检验，"
        "并在「未覆盖的分解层」中显式声明。移出而不是照跑，是因为现金流一旦反对，"
        "结论就会去否定一个用户没有提出的命题。",
    ),
    (ThesisType.DIVERGENCE, 1, "收入利润 + 盈利质量 + 现金流"): (
        "none",
        "",
        "与产品默认口径一致（最严格的一档），下游取数与判定不变。",
    ),
    (ThesisType.DIVERGENCE, 2, "发生过"): (
        "limitation",
        "",
        "用户声明时间窗内发生过重大资产重组或主营变更。同口径的同比基础被破坏，"
        "本轮全部子问题仍会照常计算，但结论的适用边界会加一条声明："
        "这些同比数字不具备前后可比性，不能直接读作经营层面的变化。",
    ),
    (ThesisType.DIVERGENCE, 2, "未发生"): (
        "none",
        "",
        "用户确认时间窗内未发生重大资产重组或主营变更，同比口径成立，本轮无需追加边界声明。",
    ),
    (ThesisType.DIVERGENCE, 2, "不确定"): (
        "limitation",
        "",
        "用户表示不确定时间窗内是否发生过重大资产重组或主营变更。本轮按「未发生」处理，"
        "该假设未经核实，会作为一条边界声明写进结论：若实际发生过，同比数据的口径可比性被破坏，"
        "本结论的适用性随之下降。用户明确表示不知道时，这条声明就不再是多余的。",
    ),
    # --- 归因型 ---
    (ThesisType.ATTRIBUTION, 0, "归母净利润"): (
        "none",
        "",
        "与产品默认主口径一致，SQ-03 仍以扣非口径做交叉检验，下游不变。",
    ),
    (ThesisType.ATTRIBUTION, 0, "扣非净利润"): (
        "limitation",
        "",
        "命题口径被指定为扣非净利润。本轮子问题中的净利润相关项仍按归母口径取数"
        "（合并利润表口径），非经常性损益的影响由 SQ-03 单独度量。"
        "结论对扣非口径的适用性由此变成一条需要读者自行判断的边界，会写入适用边界。",
    ),
    (ThesisType.ATTRIBUTION, 0, "营业利润"): (
        "limitation",
        "",
        "命题口径被指定为营业利润。本产品取的是合并利润表中的归母净利润与扣非加权ROE，"
        "未单独取营业利润科目，因此结论对营业利润口径的适用性未经检验，会写入适用边界。",
    ),
    (ThesisType.ATTRIBUTION, 1, "上年同期"): (
        "none",
        "",
        "与产品默认基期一致（同 fiscal_period 同比），下游取数与判定不变。",
    ),
    (ThesisType.ATTRIBUTION, 1, "上一季度"): (
        "limitation",
        "",
        "基期被指定为上一季度（环比）。本轮全部子问题按「同 fiscal_period 同比」构造，"
        "不改为环比 —— 换基期会同时改变口径可比性与阈值判定，不能在澄清环节悄悄发生。"
        "结论对环比口径的适用性未经检验，会写入适用边界。",
    ),
    (ThesisType.ATTRIBUTION, 1, "三年前同期"): (
        "limitation",
        "",
        "基期被指定为三年前同期。本轮子问题取的是最新报告期与上一年同一 fiscal_period，"
        "未回溯三年，因此结论对三年跨度口径的适用性未经检验，会写入适用边界。",
    ),
    (ThesisType.ATTRIBUTION, 2, "包含"): (
        "none",
        "",
        "与产品默认口径一致（按报表口径），下游取数与判定不变。",
    ),
    (ThesisType.ATTRIBUTION, 2, "仅含成熟业务"): (
        "unverifiable",
        "SQ-01",
        "命题口径被收窄为「仅含成熟业务」。要按该口径重算营收同比，必须有分部收入，"
        "而扶摇公开接口不提供分部数据 —— 这正是本产品已声明的 MIX 跳过层的同一个缺口。"
        "因此 SQ-01 判为无法验证，而不是用报表口径的合并营收冒充「成熟业务收入」。",
        "",
        "按业务分部拆分的营业收入（分部收入，扶摇公开接口不提供）",
    ),
    (ThesisType.ATTRIBUTION, 2, "不确定"): (
        "limitation",
        "",
        "用户表示不确定「新业务」是否算作主营业务，本轮按报表口径（包含）处理，"
        "该假设未经核实，会作为一条边界声明写进结论：若用户本意是剔除尚未形成"
        "可持续盈利能力的新业务，SQ-01 的收入同比读数会偏乐观。"
        "用户明确表示不知道时，这条声明就不再是多余的。",
    ),
    # --- 传导型 ---
    #
    # 第 0 问（外部变量）的四个选项此前都写着「会把『需要什么数据』写成用户真正关心的那一个」，
    # 而 limitation 这条路径**只往结论的适用边界追加一句话**，从不触碰任何证据卡上的文字。
    # 于是四个选项各自承诺了一次改写，四次都没发生 —— 「说了它没做的事」，又一次。
    # 现在按实际动作改写承诺：写进结论的适用边界，不改卡片。
    (ThesisType.TRANSMISSION, 0, "原材料价格"): (
        "limitation",
        "",
        "外部变量被明确为「原材料价格」。这不改变本轮结论 —— 无论用户指哪个变量，"
        "本数据源都无法观测它 —— 因此它写入的是**结论的适用边界**："
        "本产品要观测的是原材料价格，而该变量及其与成本端的传导关系不在取数范围内。",
    ),
    (ThesisType.TRANSMISSION, 0, "下游需求"): (
        "limitation",
        "",
        "外部变量被明确为「下游需求」。这不改变本轮结论（该变量在本数据源中不可观测），"
        "它写入的是结论的适用边界：本产品要观测的是下游需求量或订单类指标，"
        "而该口径不在取数范围内。",
    ),
    (ThesisType.TRANSMISSION, 0, "政策变化"): (
        "limitation",
        "",
        "外部变量被明确为「政策变化」。这不改变本轮结论（该变量在本数据源中不可观测），"
        "它写入的是结论的适用边界：本产品要观测的是相关政策文件与生效时点，"
        "而该口径不在取数范围内。",
    ),
    (ThesisType.TRANSMISSION, 0, "行业景气度"): (
        "limitation",
        "",
        "外部变量被明确为「行业景气度」。这类变量本身缺少可观测定义，"
        "是本产品判该命题无法验证的直接原因之一；确认这一取值会作为一条适用边界"
        "写进结论：本产品要观测的是行业景气度，而它既无统一定义、也不在取数范围内。",
    ),
    (ThesisType.TRANSMISSION, 1, "一个季度内"): (
        "limitation",
        "",
        "传导时滞被设定为「一个季度内」。本轮子问题取的是最近若干报告期，"
        "窗口已覆盖该时滞；结论的适用边界会写明：若一个季度内成本端未见变化，"
        "在本设定下即构成对传导链的证伪。",
    ),
    (ThesisType.TRANSMISSION, 1, "半年内"): (
        "none",
        "",
        "与产品默认观察窗一致（2 个报告期），下游取数与判定不变。",
    ),
    (ThesisType.TRANSMISSION, 1, "一年以上"): (
        "limitation",
        "",
        "传导时滞被设定为「一年以上」。本轮的观察窗没有覆盖该时滞，"
        "因此「现在看不到效果」在本设定下不构成证伪，这会写入结论的适用边界 —— "
        "否则读者会把「尚未传导到」误读成「不会传导」。",
    ),
    (ThesisType.TRANSMISSION, 2, "上游"): (
        "unverifiable",
        "SQ-01",
        "用户把标的在链条中的位置断言为「上游」。该位置需要主营构成来确认，"
        "而扶摇公开接口不提供分部收入，本产品无法核实该断言。"
        "SQ-01 因此判为无法验证，且结论会声明：链条位置由用户断言、未经核实。",
        "链条位置「上游」由用户在澄清环节断言，本产品**未经核实**——"
        "核实它需要主营构成，而该数据不在本产品的取数范围内。"
        "结论中凡出现「标的位置」处，均按用户断言处理。",
        "主营构成（按业务/产品/地区拆分的分部收入，用于确认标的在链条中的位置）",
    ),
    (ThesisType.TRANSMISSION, 2, "中游"): (
        "unverifiable",
        "SQ-01",
        "用户把标的在链条中的位置断言为「中游」。同上：主营构成不可得，该断言无法核实，"
        "SQ-01 判为无法验证，链条位置在结论中标注为「用户断言、未经核实」。",
        "链条位置「中游」由用户在澄清环节断言，本产品**未经核实**——"
        "核实它需要主营构成，而该数据不在本产品的取数范围内。"
        "结论中凡出现「标的位置」处，均按用户断言处理。",
        "主营构成（按业务/产品/地区拆分的分部收入，用于确认标的在链条中的位置）",
    ),
    (ThesisType.TRANSMISSION, 2, "下游"): (
        "unverifiable",
        "SQ-01",
        "用户把标的在链条中的位置断言为「下游」。同上：主营构成不可得，该断言无法核实，"
        "SQ-01 判为无法验证，链条位置在结论中标注为「用户断言、未经核实」。",
        "链条位置「下游」由用户在澄清环节断言，本产品**未经核实**——"
        "核实它需要主营构成，而该数据不在本产品的取数范围内。"
        "结论中凡出现「标的位置」处，均按用户断言处理。",
        "主营构成（按业务/产品/地区拆分的分部收入，用于确认标的在链条中的位置）",
    ),
    (ThesisType.TRANSMISSION, 2, "不确定"): (
        "none",
        "",
        "与产品默认处理一致：不做默认假设，直接检验主营构成是否可得，下游不变。",
    ),
}

_CUSTOM_ANSWER_IMPACT = (
    "该回答不在本问题的预设选项内。本产品把它原样写进 v2 的前提，"
    "但**不会**据此改动任何子问题的取数与判定 —— 自定义口径没有对应的数据与判据，"
    "硬套一个近似口径，比承认做不到更危险。"
)


class AnswerEffect(NamedTuple):
    """`ANSWER_EFFECTS` 一行的规范化形态。表里那几项都可以省略，
    这里统一补成定长字段，调用方按名字取——按位置解包的话，加一项就要改一圈调用点。"""

    kind: str = "none"
    target: str = ""
    text: str = ""
    limitation: str = ""
    what_is_needed: str = ""


def _lookup_effect(ttype: ThesisType, idx: int, answer: str) -> AnswerEffect:
    """查这张表，补齐成 `AnswerEffect`。查不到说明选项与本表不同步——
    那时宁可当无效果，也不猜一个效果出来。"""
    raw = ANSWER_EFFECTS.get((ttype, idx, answer))
    if raw is None:
        return AnswerEffect("none", "", _CUSTOM_ANSWER_IMPACT)
    kind, target, text, *rest = raw
    return AnswerEffect(
        kind,
        target,
        text,
        rest[0] if len(rest) > 0 else "",
        rest[1] if len(rest) > 1 else "",
    )


def _clarifications(
    ttype: ThesisType, answers: Optional[dict[str, str]] = None
) -> list[ClarificationQuestion]:
    """构造澄清问题。

    用户答了就用用户的原话（`answer`），没答才落回默认假设（`assumption`）。

    **题面就是回答的键**，所以本函数必须用「最终确定的命题类型」调用：类型一变，
    整组题面换掉，上一轮的回答会一条也对不上。调用点因此放在 LLM 类型判定之后
    —— 放在它之前，会出现「题面取自规则类型、效果按 LLM 类型执行」的错位：
    界面上写着「该子问题判为无法验证」，下游一个字节都没动。

    「未回答路径逐字不变」这条约束在**默认假设文案**上被改过两处，方向是往真相那边：
    背离型第 3 问与归因型第 2 问原本各带一句「会在结论的适用边界中声明该假设」，
    而这两句从未兑现（默认路径上 `conclusion.limitations` 里根本没有它们）。
    兑现不了的承诺只能删掉或改成真话；判定、证据与边界条数在默认路径上没有变。
    """
    answers = answers or {}
    out: list[ClarificationQuestion] = []
    for idx, (q, why, opts, default) in enumerate(CLARIFY[ttype]):
        given = (answers.get(q) or "").strip()
        if not given:
            out.append(
                ClarificationQuestion(
                    question=q,
                    why_it_matters=why,
                    options=opts,
                    assumption=f"用户未指定时，本产品采用：{default}。该假设会随结论一并展示，"
                               f"并可在界面上修改后重跑。",
                )
            )
            continue
        eff = _lookup_effect(ttype, idx, given)
        out.append(
            ClarificationQuestion(
                question=q,
                why_it_matters=why,
                options=opts,
                assumption=f"由用户在澄清环节指定：{given}。",
                answer=given,
                impact=(
                    eff.text if eff.kind != "none"
                    else (eff.text or "与本产品默认取值一致，下游不变。")
                ),
                effect_kind=eff.kind,
            )
        )
    return out


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

    # 前提取自哪里必须分开写：用户答的取 answer，未答的才从默认假设的文案里截。
    # 早先一律靠 `split("：", 1)[-1]` 从 assumption 里截，回答里只要有一个全角冒号
    # （例如「与同业比：沪深300」），截出来就只剩冒号后面的半句——v2 会悄悄丢掉用户的前半句。
    def _premise(c: ClarificationQuestion) -> str:
        if c.answer:
            return c.answer
        return c.assumption.split("：", 1)[-1].rstrip("。")

    assumptions = "；".join(_premise(c) for c in clarifications)
    tightened = (
        f"关于 {subject}，在「{horizon}」的时间窗内，"
        f"{raw.strip().rstrip('。')}。"
        f"（本命题的可验证化前提：{assumptions}）"
    )
    n_answered = sum(1 for c in clarifications if c.answer)
    source = (
        f"其中 {n_answered} 条前提由用户在澄清环节指定，其余由本产品补全"
        if n_answered
        else "上述前提由本产品在澄清环节自动补全"
    )
    v2 = ThesisVersion(
        version="v2",
        text=tightened,
        horizon=horizon,
        decision_context=(
            f"命题类型判定为「{ttype.value}」。{source}，"
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
                 thscode: Optional[str] = None,
                 answers: Optional[dict[str, str]] = None) -> ParsedThesis:
    """解析命题。优先 LLM，失败或无凭据时退回规则引擎。

    `answers` 是用户在澄清环节给出的回答，键为澄清问题原文。
    带回答重跑时**不采用 LLM 生成的澄清问题**：回答是按上一轮界面上显示的问题填的，
    而 LLM 每次生成的问题不可复现，一旦换了一套问题，回答就会静默地对不上任何一条 ——
    「用户答了但产品没听见」比「不让答」更糟。类型判定仍可走 LLM。

    因此 `_clarifications` 在 LLM 之后才调用（它要拿到最终类型），
    并且对不上的回答一律记进 `unmatched_answers` 让前端说话，不再静默丢弃。
    """
    code, nm = _detect_subject(raw)
    # 命题里写全了「600519.SH」就直接用，不必再查一遍
    thscode = thscode or _detect_thscode(raw)
    ticker = resolved_ticker or code
    name = resolved_name or nm

    ttype, rationale = _detect_type(raw)
    horizon = _detect_horizon(raw)
    engine = "规则引擎"
    used_llm = False
    llm_clar: list[ClarificationQuestion] = []

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
                    # 先存着，等拿到最终类型再决定用不用它——见下面那一段。
                    llm_clar = [
                        ClarificationQuestion(
                            question=a.get("question", ""),
                            why_it_matters=a.get("why", ""),
                            options=[],
                            assumption=a.get("default_assumption", ""),
                        )
                        for a in amb
                        if a.get("question")
                    ]
                engine = "Claude 解析（规则引擎已并行校验）"
                used_llm = True
            except (ValueError, TypeError):
                pass

    # 澄清问题在这里才构造，用的是**最终**的命题类型。
    #
    # 此前它在 LLM 之前用规则类型构造，而 LLM 可以把类型改成另一类：
    # 于是「题面与 impact 文案」取自规则类型，「实际执行的动作」按 LLM 类型查表 ——
    # 两边查不到一起时，界面上写着「该子问题判为无法验证」，下游一个字节都没动，
    # errors 里也没有痕迹。改成一处调用点，两边就必然同源。
    #
    # 带回答重跑时不用 LLM 生成的问题：那些问题每次都不一样，回答是照上一轮的题面填的。
    clar = llm_clar if (llm_clar and not answers) else _clarifications(ttype, answers)

    # 「用户答了但产品没听见」必须能被看见。回答以题面为键，而题面由命题类型生成：
    # 用户改一句命题、或 AI 把类型判成了另一类，整组题面就换掉，上一轮的回答
    # 会一条也对不上 —— 此时按默认假设继续是对的，但必须说出来（此后端返回给前端）。
    given_answers = [(v or "").strip() for v in (answers or {}).values()]
    given_answers = [a for a in given_answers if a]
    heard = {c.answer for c in clar if c.answer}
    unmatched = sorted({a for a in given_answers if a not in heard})

    v1, v2, diffs = _revise(ttype, raw, name or ticker or "该标的", horizon, clar)

    # ParsedThesis 的约束「给了 ticker 就必须给 thscode」本身是对的（不许猜后缀），
    # 但它不该被撞成异常：只有 6 位裸代码时**主动不认标的**，由 run.py 的消歧段
    # 如实报出「未能确定标的」。线上实测出过这个 500——
    # 命题写成「我认为 600887 的估值…」时异常直接穿到 API。
    if ticker and not thscode:
        ticker = None

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
        parsed_by="llm" if used_llm else "rule_engine",
        unmatched_answers=unmatched,
    )

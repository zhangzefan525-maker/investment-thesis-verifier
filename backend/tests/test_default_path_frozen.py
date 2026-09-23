"""默认路径的冻结基线：一句都不回答时，四条例题必须逐字保持现状。

## 这条测试为什么必须与「冻结基线」比，而不是自己和自己比

早先这里放着一条 `test_no_answers_is_byte_identical_to_before`，它的写法是
「同一份代码，不传 answers 跑一次、传空字典跑一次，两边相等」。那条断言恒真，
也永远抓不到真正要防的东西：**改动的代码把默认路径悄悄改了**。
它守住的只是「两种不传参的写法等价」，名字里的「与既有行为逐字相同」是过度声明。

真正发生过的漂移就是这么溜过去的：澄清功能把背离型第 3 问的默认假设文案改了一句，
又顺手把支持型结论里的「收入、利润**与**盈利质量」改成顿号连接，
两处都落在**用户一句都没回答**的路径上，而四条例题的结论是交付物的一部分
（README、文档、演示视频都引用了它们）。当时 216 条测试一条都没红。

因此这里改成与一份**检入仓库的**基线比对。基线是这一层唯一的真相来源，
任何落在默认路径上的改动都必须显式地重新冻结它（见文件末尾的 `--freeze`），
并在 `docs/修改记录.md` 与 `docs/AI使用与验证记录.md` 里说明改了什么、为什么改。
「顺手改掉一句措辞」不会再有第二次。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.engine.run import run_verification
from app.providers.fixture import FixtureProvider
from tests.conftest import CASES

GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "default_path_golden.json"


# --------------------------------------------------------------------------
# 投影：只留**用户看得见**的东西
#
# 不整份 dump 的原因有两个：一是整份里最大的一块是重建 PE 序列（每例 361–371 个点，
# 四例合计 16KB），而它完全由 fixtures 决定，冻在这里只是把同一份数据抄第二遍；
# 二是基线要能被人读、被人 review，一串几万行的浮点数组做不到这一点。
# 图上只冻「形状」：有几条序列、首尾点、分位、窗口。
# --------------------------------------------------------------------------


def _chart(block: dict | None) -> dict | None:
    if not block:
        return None
    out: dict = {
        "withheld": [
            [w["name"], w["backs_sub_question"], w["reason"]] for w in block.get("withheld", [])
        ]
    }
    for key in ("valuation", "profit", "margin"):
        v = block.get(key)
        if v is None:
            out[key] = None
            continue
        rest = {k: val for k, val in v.items() if k != "series"}
        pts = v.get("series") or []
        rest["series"] = (
            {"n": len(pts), "labels": [pts[0]["label"], pts[-1]["label"]],
             "values": [pts[0]["value"], pts[-1]["value"]]}
            if pts
            else None
        )
        out[key] = rest
    return out


def project(run) -> dict:
    """把一次运行投影成基线里冻结的那一份。生成与比对共用它，两边不会各写一套。"""
    d = run.model_dump(mode="json")
    p, dec = d["parsed"], d["decomposition"]
    con = d["conclusion"] or {}
    return {
        "thesis_type": p["thesis_type"],
        "parsed_by": p["parsed_by"],
        "type_rationale": p["type_rationale"],
        "v2_text": p["v2"]["text"],
        "diffs": [[x["field"], x["before"], x["after"]] for x in p["diffs"]],
        "clarifications": [
            {
                k: c.get(k)
                for k in ("question", "options", "assumption", "answer", "impact", "effect_kind")
            }
            for c in p["clarifications"]
        ],
        "unmatched_answers": p["unmatched_answers"],
        "sub_questions": [s["id"] for s in dec["sub_questions"]],
        "skipped_layers": dec["skipped_layers"],
        "generated_by": dec["generated_by"],
        "evidence": [
            {
                k: e.get(k)
                for k in (
                    "id", "sub_question_id", "claim", "display_value", "verdict",
                    "confidence", "reasoning", "decision_rule_applied",
                    "threshold_applied", "fact_or_logic",
                )
            }
            for e in d["evidence"]
        ],
        "unverifiable": [
            {
                "id": e["id"],
                **{
                    k: e["unverifiable"][k]
                    for k in ("category", "what_is_needed", "where_to_get", "failure_evidence")
                },
            }
            for e in d["evidence"]
            if e.get("unverifiable")
        ],
        "charts": _chart(d["charts"]),
        "conclusion": {
            k: con.get(k)
            for k in (
                "verdict", "statement", "coverage_note", "limitations",
                "conflicts", "falsification_conditions",
                "support_count", "refute_count", "unverifiable_count",
            )
        },
        "errors": d["errors"],
        "answer_journal": d["answer_journal"],
        "data_mode": d["data_mode"],
    }


def _current() -> dict:
    return {
        name: project(run_verification(text, provider=FixtureProvider()))
        for name, text in CASES.items()
    }


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN_PATH.exists():  # pragma: no cover - 基线缺失是配置错误，不是产品行为
        pytest.fail(
            f"默认路径基线不存在：{GOLDEN_PATH}。"
            f"用 `python -m tests.test_default_path_frozen --freeze` 生成，"
            f"并在文档里说明这次为什么动默认路径。"
        )
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_default_path_matches_the_frozen_baseline(golden):
    """一句都不回答时，四条例题的产物必须与冻结基线逐字段相同。

    失败时**先读 diff 再改基线**：这条测试红，说明有改动落到了默认路径上。
    要么那是 unintended 的、应当改回去；要么它是有意的，那就得说清为什么，
    再重新冻结 —— 而不是把基线覆盖掉了事。
    """
    cur = _current()
    problems: list[str] = []
    for case in CASES:
        if case not in golden:
            problems.append(f"{case}: 基线里没有这一例，基线需要重新冻结")
            continue
        if cur[case] == golden[case]:
            continue
        for key in sorted(set(cur[case]) | set(golden[case])):
            a, b = golden[case].get(key), cur[case].get(key)
            if a != b:
                problems.append(f"{case}.{key}:\n  基线 = {a!r}\n  现在 = {b!r}")
    assert not problems, "默认路径相对冻结基线发生了变化：\n\n" + "\n\n".join(problems)


def test_baseline_covers_every_case(golden):
    """基线必须覆盖 conftest 里的全部例题。少一例，那一例就无人看守。"""
    assert set(golden) == set(CASES)


def test_projection_drops_only_volatile_and_bulky_fields(golden):
    """投影不能把「会变的东西」也冻进来，否则这条测试会因为噪声天天红。

    运行标识、时间戳、以及重建序列的逐点数值都不进基线；它们在别处另有测试守着。
    """
    blob = json.dumps(golden, ensure_ascii=False)
    for volatile in ("run_id", "RUN-", "created_at", "fetched_at"):
        assert volatile not in blob, f"基线里冻进了易变字段 {volatile}"
    for case in CASES:
        charts = golden[case]["charts"] or {}
        for key in ("valuation", "profit", "margin"):
            v = charts.get(key)
            if v and v.get("series"):
                # 只留首尾两点与点数。逐点数值完全由 fixtures 决定，
                # 冻进来只是把同一份数据抄第二遍，还让基线没法被人读。
                assert set(v["series"]) == {"n", "labels", "values"}, v["series"].keys()
                assert len(v["series"]["labels"]) == 2


if __name__ == "__main__":  # pragma: no cover - 人工重新冻结入口
    if "--freeze" not in sys.argv:
        print(__doc__)
        raise SystemExit("用法：python -m tests.test_default_path_frozen --freeze")
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _current()
    GOLDEN_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    print(f"已重新冻结默认路径基线：{GOLDEN_PATH}")

"""把同一组命题打到两个（或多个）后端上，逐字段比对结论。

用途：**证明产品在实时模式与冻结快照模式下给出同一个结论**。
两种模式的数据来源不同（实时接口 vs 已捕获快照），但判定逻辑是同一条；
如果哪次改动让模式影响了结论，这张表就会对不上。

用法：
    # 实时模式的本机后端（本机配了凭据时）
    uvicorn app.main:app --port 8000 &
    python scripts/mode_compare.py --base http://127.0.0.1:8000 --base http://39.96.194.197/thesis

    # 想同时比快照模式：把凭据藏起来再起一个后端（不改动真实凭据文件）
    APPDATA=/nonexistent USERPROFILE=/nonexistent uvicorn app.main:app --port 8002

输出是一张对齐表 + 逐例的 IDENTICAL / DIFFERS 判定，退出码非 0 表示有差异。
只报告结论与条数，不接受也不输出任何凭据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

import httpx

CASES: list[tuple[str, str]] = [
    ("茅台 · 背离型（例题原文）", "我认为贵州茅台的估值已经回落，但基本面并没有恶化"),
    ("平安 · 背离型（例题原文）", "我认为中国平安的估值已经回落，但基本面并没有恶化"),
    ("宁德 · 归因型（例题原文）", "我认为宁德时代的盈利改善来自主营业务"),
    ("宁德 · 传导型（例题原文）", "我认为碳酸锂价格上涨会挤压宁德时代的利润空间"),
    ("茅台 · 写全代码", "我认为贵州茅台（600519.SH）的估值已经回落，但基本面并没有恶化"),
    ("宁德 · 归因型（写全代码）", "我认为宁德时代（300750.SZ）的盈利改善来自主营业务"),
    ("裸代码", "我认为 600887 的估值已经回落，但基本面并没有恶化"),
]

# 比对哪些字段。`statement` 不直接比字符串，比的是它的哈希——
# 文案里有断言用的 `**` 标记，肉眼比对容易漏掉一个标点。
COMPARED = (
    "status",
    "verdict",
    "support",
    "refute",
    "unverifiable",
    "evidence",
    "falsification",
    "subquestions",
    "statement_sha",
)


def probe(base: str, client: httpx.Client) -> list[dict]:
    rows = []
    for name, text in CASES:
        r = client.post(base + "/api/verify", json={"raw_text": text})
        row: dict = {"case": name, "text": text, "status": r.status_code}
        if r.status_code == 200:
            d = r.json()
            con = d.get("conclusion") or {}
            row.update(
                verdict=con.get("verdict"),
                support=con.get("support_count"),
                refute=con.get("refute_count"),
                unverifiable=con.get("unverifiable_count"),
                evidence=len(d.get("evidence") or []),
                falsification=len(con.get("falsification_conditions") or []),
                subquestions=len((d.get("decomposition") or {}).get("sub_questions") or []),
                statement_sha=hashlib.sha256(
                    (con.get("statement") or "").encode("utf-8")
                ).hexdigest()[:12],
                thscode=(d.get("parsed") or {}).get("thscode"),
                errors=len(d.get("errors") or []),
            )
        else:
            row["body"] = r.text[:200]
        rows.append(row)
    return rows


def fmt(row: dict) -> str:
    return "%-12s %-9s %s" % (
        row.get("verdict"),
        "%s/%s/%s" % (row.get("support"), row.get("refute"), row.get("unverifiable")),
        "ev=%s fals=%s sq=%s stmt=%s" % (
            row.get("evidence"),
            row.get("falsification"),
            row.get("subquestions"),
            row.get("statement_sha"),
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="实时模式与快照模式的结论比对")
    ap.add_argument("--base", action="append", required=True, help="后端根地址，可重复")
    ap.add_argument("--out", help="把原始结果写成 JSON，便于事后复核")
    args = ap.parse_args()

    report: dict = {"bases": [], "cases": []}
    with httpx.Client(timeout=180) as client:
        for base in args.base:
            health = client.get(base + "/api/health").json()
            report["bases"].append(
                {
                    "base": base,
                    "live_available": health.get("live_available"),
                    "credential_origin": health.get("credential", {}).get("origin"),
                }
            )
            report["cases"].append({"base": base, "rows": probe(base, client)})

    for b in report["bases"]:
        print("== %s  实时可用=%s  凭据来源=%s" % (b["base"], b["live_available"], b["credential_origin"]))

    first = report["cases"][0]["rows"]
    others = report["cases"][1:]
    differ = 0
    for i, row in enumerate(first):
        print("\n[%d] %s" % (i + 1, row["case"]))
        print("    %-46s %s" % (report["bases"][0]["base"], fmt(row)))
        for other, other_base in zip(others, report["bases"][1:]):
            orow = other["rows"][i]
            same = all(row.get(k) == orow.get(k) for k in COMPARED)
            if not same:
                differ += 1
            print("    %-46s %s  %s" % (other_base["base"], fmt(orow), "IDENTICAL" if same else "**DIFFERS**"))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print("\n原始结果已写入 %s" % args.out)

    print("\n合计：%d 例，%d 处不一致" % (len(first), differ))
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())

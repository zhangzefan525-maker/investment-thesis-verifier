"""冻结快照数据源。

## 用途与约束

两个用途：
1. **测试**：不依赖网络和凭据就能跑通全链路，包括**失败路径**。
2. **演示一致性**：录视频当天与评委查看当天，看到的是同一份数据。
   实时行情会变，冻结快照不会。

## 硬约束

按 `hithink-finance` skill 的规定：「真实数据不可用时报告原因，
**不用近似数据、静态示例或模拟数据冒充**」。

因此 FixtureProvider 的数据**只能来自对扶摇接口的真实调用捕获**
（见 `scripts/capture.py`），不得手工编造。每条 fixture 都带原始 `request_id`
和抓取时间戳，UI 上也会明确标注当前处于 fixture 模式。

缺少某路 fixture 时，返回的是 `Err` 而不是空数组——
这样失败路径的测试才是有意义的。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..schemas import Provenance, UnverifiableCategory, UnverifiableDetail
from .base import Err, FetchResult, Ok

SOURCE_NAME = "同花顺扶摇（冻结快照）"


class FixtureProvider:
    """从 fixtures/ 目录读取已捕获的真实返回。"""

    name = SOURCE_NAME

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path(__file__).resolve().parents[2] / "fixtures"

    # -- 内部 ---------------------------------------------------------------

    def _dir(self, thscode: str) -> Path:
        return self.root / thscode.replace(".", "_")

    def _load(self, thscode: str, key: str) -> Optional[dict]:
        path = self._dir(thscode) / f"{key}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _serve(
        self,
        thscode: str,
        key: str,
        report_period: str,
        caliber: str,
        unit: str,
        what_is_needed: str,
    ) -> FetchResult[Any]:
        blob = self._load(thscode, key)
        if blob is None:
            return Err(
                detail=UnverifiableDetail(
                    category=UnverifiableCategory.SOURCE_UNREACHABLE,
                    what_is_needed=what_is_needed,
                    where_to_get=(
                        f"运行 scripts/capture.py 捕获 {thscode} 的 {key} 真实返回，"
                        f"或配置 HITHINK_FINANCE_API_KEY 切换到实时模式"
                    ),
                    failure_evidence=(
                        f"fixture 文件不存在：{self._dir(thscode) / (key + '.json')}。"
                        f"按本项目的硬约束，此处**不用模拟数据顶替**，如实报告缺数据"
                    ),
                ),
                endpoint=f"(fixture: {key})",
            )

        captured_at = blob.get("_captured_at", "未知时间")
        captured = Provenance(
            source=SOURCE_NAME,
            endpoint=blob.get("_endpoint", f"(fixture: {key})"),
            request_params=blob.get("_params", {}),
            report_period=report_period,
            caliber=caliber,
            unit=unit,
            request_id=blob.get("_request_id"),
            fetched_at=_parse_ts(captured_at),
            raw={
                "_fixture": True,
                "_captured_at": captured_at,
                "note": "本数据来自对扶摇接口的真实调用的冻结快照，非实时数据",
            },
        )
        return Ok(value=blob.get("data"), provenance=captured)

    def available(self) -> dict[str, list[str]]:
        """列出已捕获的 fixture，供 /api/health 展示。"""
        out: dict[str, list[str]] = {}
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            if d.is_dir():
                out[d.name] = sorted(p.stem for p in d.glob("*.json"))
        return out

    # -- DataProvider 接口 --------------------------------------------------

    def search_ticker(self, query: str) -> FetchResult[dict]:
        """在已捕获的快照里检索标的。

        离线模式下没有真正的代码表可查，于是**只认已捕获的那几个标的**，
        并支持用代码、带后缀代码或中文简称命中。命中范围窄是刻意的：
        宁愿如实说「离线模式下找不到」，也不假装自己能检索全市场。
        """
        q = (query or "").strip().upper()
        if not q:
            return _miss(query, self.root)
        for d in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not d.is_dir():
                continue
            blob = self._load(d.name.replace("_", ".", 1), "search_ticker")
            items = (blob or {}).get("data") or []
            for item in items:
                thscode = str(item.get("thscode", ""))
                ticker = str(item.get("ticker", ""))
                name = str(item.get("name", ""))
                if q in {thscode.upper(), ticker.upper(), name.upper()} or (
                    name and name in q
                ) or (ticker and ticker in q):
                    return Ok(
                        value=[item],
                        provenance=Provenance(
                            source=SOURCE_NAME,
                            endpoint="(fixture: search_ticker)",
                            request_params={"_query": query, "_matched": thscode},
                            report_period="代码表当前快照",
                            caliber="离线子串匹配（仅限已捕获标的）",
                            unit="-",
                            request_id=(blob or {}).get("_request_id"),
                            fetched_at=_parse_ts((blob or {}).get("_captured_at", "")),
                            raw={
                                "_fixture": True,
                                "note": "该命中来自冻结快照的标的表，非实时检索",
                            },
                        ),
                    )
        return _miss(query, self.root)

    def price_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]:
        return self._serve(thscodes[0], "price_snapshot", "最新交易日", "不复权", "CNY", "行情快照")

    def price_historical(
        self, thscode: str, start_ms: int, end_ms: int, adjust: str = "forward"
    ) -> FetchResult[list[dict]]:
        key = "prices_historical" if adjust == "forward" else f"prices_historical_{adjust}"
        return self._serve(
            thscode, key, f"{_d(start_ms)} ~ {_d(end_ms)}",
            f"日线 / {adjust} 复权", "CNY", "历史 K 线",
        )

    def valuation_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]:
        return self._serve(
            thscodes[0], "valuations_snapshot", "最新交易日",
            "PE-TTM / PE-MRQ / PB-MRQ / PS-TTM / PCF-TTM", "倍", "估值快照",
        )

    def income_statements(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        key = "income_statements" if period == "quarterly" else "income_statements_annual"
        return self._serve(
            thscode, key, f"最近 {limit} 期（{period}）",
            "整体合并报表（consolidated）", "原币元", "合并利润表",
        )

    def balance_sheets(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        key = "balance_sheets" if period == "annual" else "balance_sheets_q"
        return self._serve(
            thscode, key, f"最近 {limit} 期（{period}）",
            "整体合并报表（consolidated）", "原币元", "合并资产负债表",
        )

    def cash_flow_statements(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        # 键名必须与 scripts/capture.py 写入的完全一致，否则离线模式会
        # 静默缺一路数据（此前 annual 请求去找不存在的
        # cash_flow_statements_annual，导致离线跑出来的结论与实时不一致）
        key = "cash_flow_statements" if period == "annual" else "cash_flow_statements_q"
        return self._serve(
            thscode, key, f"最近 {limit} 期（{period}）",
            "整体合并报表（consolidated）", "原币元", "合并现金流量表",
        )

    def financial_indicators(self, thscode: str, report: str) -> FetchResult[dict]:
        return self._serve(
            thscode, "financial_indicators", report,
            "单报告期五类指标", "按指标而定（百分比/倍/次）", "财务指标",
        )


def _miss(query: str, root: Path) -> Err:
    have = sorted(d.name.replace("_", ".", 1) for d in root.iterdir()) if root.is_dir() else []
    return Err(
        detail=UnverifiableDetail(
            category=UnverifiableCategory.SOURCE_UNREACHABLE,
            what_is_needed=f"离线快照中存在与 {query!r} 匹配的标的",
            where_to_get=(
                f"当前离线快照仅覆盖 {len(have)} 个标的：{'、'.join(have) or '（空）'}。"
                f"新增标的需要运行 scripts/capture.py，或配置 HITHINK_FINANCE_API_KEY 切换实时模式"
            ),
            failure_evidence=(
                f"离线检索 query={query!r} 在已捕获标的表中无命中。"
                f"离线模式不具备全市场检索能力，此处如实报缺失，不做模糊匹配"
            ),
        ),
        endpoint="(fixture: search_ticker)",
    )


def _parse_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")

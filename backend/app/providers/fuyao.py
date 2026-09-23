"""扶摇（同花顺金融数据服务）REST 客户端。

契约来源：HiThink-Tech/Financial-API 官方 Skill `hithink-finance`
- Base URL https://fuyao.aicubes.cn
- 认证 Header `X-api-key`
- HTTP 恒为 200，**必须**以 `code == 0` 判断成功
- 空值 `null` 表示未披露，**不得补零**

凭据读取顺序（官方约束）：
  1. 进程环境变量 HITHINK_FINANCE_API_KEY
  2. 用户级 credentials.env（Windows: %APPDATA%\\hithink-finance\\credentials.env）
**Key 不进入代码、日志、输出、项目文件或 Git。**
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from ..schemas import Provenance, UnverifiableCategory, UnverifiableDetail
from .base import Err, FetchResult, Ok, failure_from_code

BASE_URL = "https://fuyao.aicubes.cn"
SOURCE_NAME = "同花顺扶摇"

RETRYABLE = {4001, 5001, 5002, 5003}
MAX_RETRY = 3


def load_api_key() -> Optional[str]:
    """按官方顺序读取凭据。只返回 Key 本身，绝不打印。"""
    key = os.environ.get("HITHINK_FINANCE_API_KEY") or os.environ.get("FUYAO_TOKEN")
    if key and key.strip():
        return key.strip()

    candidates = [
        Path(os.environ.get("APPDATA", "")) / "hithink-finance" / "credentials.env",
        Path.home() / ".config" / "hithink-finance" / "credentials.env",
        Path.home() / ".hithink-finance" / "credentials.env",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in {"HITHINK_FINANCE_API_KEY", "FUYAO_TOKEN"} and v.strip():
                    return v.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def credential_status() -> dict[str, Any]:
    """凭据状态。只报告来源与是否存在，不报告 Key 内容。"""
    if os.environ.get("HITHINK_FINANCE_API_KEY"):
        return {"present": True, "origin": "环境变量 HITHINK_FINANCE_API_KEY"}
    if os.environ.get("FUYAO_TOKEN"):
        return {"present": True, "origin": "环境变量 FUYAO_TOKEN（兼容来源）"}
    for path in [
        Path(os.environ.get("APPDATA", "")) / "hithink-finance" / "credentials.env",
        Path.home() / ".config" / "hithink-finance" / "credentials.env",
    ]:
        if path.is_file() and path.stat().st_size > 0:
            return {"present": True, "origin": f"用户级凭据文件 {path.name}"}
    return {"present": False, "origin": "未找到任何凭据来源"}


class FuyaoClient:
    """扶摇 REST 客户端。所有方法返回 Ok（带 provenance）或 Err（带三问）。"""

    name = SOURCE_NAME

    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0):
        self._api_key = api_key or load_api_key()
        self._timeout = timeout

    # -- 内部 ---------------------------------------------------------------

    def _missing_key_err(self, endpoint: str, params: dict) -> Err:
        return Err(
            detail=UnverifiableDetail(
                category=UnverifiableCategory.PERMISSION_DENIED,
                what_is_needed="一个有效的扶摇 API Key",
                where_to_get="https://fuyao.aicubes.cn/admin/ 扫码登录后创建（免费）",
                failure_evidence="本地未找到任何凭据来源：环境变量 HITHINK_FINANCE_API_KEY 与用户级 credentials.env 均为空",
            ),
            endpoint=endpoint,
            params=params,
        )

    def _get(
        self,
        endpoint: str,
        params: dict[str, Any],
        what_is_needed: str,
        where_to_get: str,
        report_period: str,
        caliber: str,
        unit: str,
    ) -> FetchResult[Any]:
        if not self._api_key:
            return self._missing_key_err(endpoint, params)

        url = f"{BASE_URL}{endpoint}"
        last_err: Optional[Err] = None

        for attempt in range(MAX_RETRY):
            try:
                resp = httpx.get(
                    url,
                    params=params,
                    headers={"X-api-key": self._api_key},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_err = Err(
                    detail=UnverifiableDetail(
                        category=UnverifiableCategory.SOURCE_UNREACHABLE,
                        what_is_needed=what_is_needed,
                        where_to_get=where_to_get,
                        failure_evidence=f"网络错误（第 {attempt + 1} 次）：{type(exc).__name__}: {exc}",
                    ),
                    endpoint=endpoint,
                    params=params,
                )
                time.sleep(0.6 * (2**attempt))
                continue

            try:
                body = resp.json()
            except ValueError:
                last_err = Err(
                    detail=UnverifiableDetail(
                        category=UnverifiableCategory.SOURCE_UNREACHABLE,
                        what_is_needed=what_is_needed,
                        where_to_get=where_to_get,
                        failure_evidence=f"HTTP {resp.status_code} 但响应体不是 JSON：{resp.text[:200]!r}",
                    ),
                    endpoint=endpoint,
                    params=params,
                )
                time.sleep(0.6 * (2**attempt))
                continue

            code = body.get("code")
            if code == 0:
                return Ok(
                    value=body.get("data"),
                    provenance=Provenance(
                        source=SOURCE_NAME,
                        endpoint=f"GET {endpoint}",
                        request_params={k: v for k, v in params.items() if k != "api_key"},
                        report_period=report_period,
                        caliber=caliber,
                        unit=unit,
                        request_id=body.get("request_id"),
                        raw={},
                    ),
                )

            err = failure_from_code(
                code=code if isinstance(code, int) else -1,
                message=str(body.get("message")),
                request_id=body.get("request_id"),
                endpoint=endpoint,
                what_is_needed=what_is_needed,
                where_to_get=where_to_get,
            )
            last_err = err
            if code in RETRYABLE and attempt < MAX_RETRY - 1:
                time.sleep(0.6 * (2**attempt))
                continue
            return err

        return last_err or Err(
            detail=UnverifiableDetail(
                category=UnverifiableCategory.SOURCE_UNREACHABLE,
                what_is_needed=what_is_needed,
                where_to_get=where_to_get,
                failure_evidence="重试耗尽且无具体错误记录",
            ),
            endpoint=endpoint,
            params=params,
        )

    # -- 对外 ---------------------------------------------------------------

    def search_ticker(self, query: str) -> FetchResult[dict]:
        r = self._get(
            "/api/meta/tickers/search",
            {"q": query, "limit": 10},
            what_is_needed="标的检索能力",
            where_to_get="扶摇 /api/meta/tickers/search",
            report_period="代码表当前快照",
            caliber="子串匹配",
            unit="-",
        )
        if not r.ok:
            return r
        items = (r.value or {}).get("item") or []
        if not items:
            return Err(
                detail=UnverifiableDetail(
                    category=UnverifiableCategory.DATA_NOT_EXIST,
                    what_is_needed=f"关键词 {query!r} 对应一个已在 A 股代码表中的标的",
                    where_to_get="请换用六位股票代码或完整中文简称重试",
                    failure_evidence=f"检索返回 code=0 但 item 为空，未匹配到任何标的（query={query!r}）",
                ),
                endpoint="/api/meta/tickers/search",
                params={"q": query},
            )
        r.value = items
        return r

    def price_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]:
        r = self._get(
            "/api/a-share/prices/snapshot",
            {"thscodes": ",".join(thscodes)},
            what_is_needed="行情快照能力",
            where_to_get="扶摇 /api/a-share/prices/snapshot",
            report_period="最新交易日",
            caliber="不复权最新成交价",
            unit="CNY",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def price_historical(
        self, thscode: str, start_ms: int, end_ms: int, adjust: str = "forward"
    ) -> FetchResult[list[dict]]:
        span_years = (end_ms - start_ms) / (365.25 * 24 * 3600 * 1000)
        if span_years > 10:
            return Err(
                detail=UnverifiableDetail(
                    category=UnverifiableCategory.CALIBER_MISMATCH,
                    what_is_needed="不超过 10 年的时间窗口",
                    where_to_get="请拆分时间窗后分次请求",
                    failure_evidence=f"请求窗口跨度 {span_years:.1f} 年，超过接口强约束的 10 年上限",
                ),
                endpoint="/api/a-share/prices/historical",
                params={"thscode": thscode},
            )
        r = self._get(
            "/api/a-share/prices/historical",
            {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms, "adjust": adjust},
            what_is_needed="历史 K 线能力",
            where_to_get="扶摇 /api/a-share/prices/historical",
            report_period=f"{_ms_to_date(start_ms)} ~ {_ms_to_date(end_ms)}",
            caliber=f"日线 / {adjust} 复权",
            unit="CNY",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def valuation_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]:
        r = self._get(
            "/api/a-share/valuations/snapshot",
            {"thscodes": ",".join(thscodes)},
            what_is_needed="估值快照能力",
            where_to_get="扶摇 /api/a-share/valuations/snapshot",
            report_period="最新交易日",
            caliber="PE-TTM / PE-MRQ / PB-MRQ / PS-TTM / PCF-TTM",
            unit="倍",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def income_statements(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        r = self._get(
            "/api/a-share/financials/income-statements",
            {"thscode": thscode, "period": period, "limit": limit},
            what_is_needed="合并利润表多期序列",
            where_to_get="扶摇 /api/a-share/financials/income-statements",
            report_period=f"最近 {limit} 期（{period}）",
            caliber="整体合并报表（consolidated）",
            unit="原币元",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def balance_sheets(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        r = self._get(
            "/api/a-share/financials/balance-sheets",
            {"thscode": thscode, "period": period, "limit": limit},
            what_is_needed="合并资产负债表多期序列",
            where_to_get="扶摇 /api/a-share/financials/balance-sheets",
            report_period=f"最近 {limit} 期（{period}）",
            caliber="整体合并报表（consolidated）",
            unit="原币元",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def cash_flow_statements(self, thscode: str, period: str, limit: int) -> FetchResult[list[dict]]:
        r = self._get(
            "/api/a-share/financials/cash-flow-statements",
            {"thscode": thscode, "period": period, "limit": limit},
            what_is_needed="合并现金流量表多期序列",
            where_to_get="扶摇 /api/a-share/financials/cash-flow-statements",
            report_period=f"最近 {limit} 期（{period}）",
            caliber="整体合并报表（consolidated）",
            unit="原币元",
        )
        if r.ok:
            r.value = (r.value or {}).get("item") or []
        return r

    def financial_indicators(self, thscode: str, report: str) -> FetchResult[dict]:
        r = self._get(
            "/api/a-share/financials/indicators",
            {"thscode": thscode, "report": report},
            what_is_needed="财务指标（盈利/成长/偿债/营运/现金流五类）",
            where_to_get="扶摇 /api/a-share/financials/indicators",
            report_period=report,
            caliber="单报告期五类指标",
            unit="按指标而定（百分比/倍/次）",
        )
        if r.ok:
            blocks = (r.value or {}).get("abilities") or []
            flat: dict[str, Optional[str]] = {}
            for block in blocks:
                for ind in block.get("indicators") or []:
                    flat[ind.get("index_id")] = ind.get("value")
            r.value = flat
        return r


def _ms_to_date(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")

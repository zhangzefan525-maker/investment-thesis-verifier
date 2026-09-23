"""数据源抽象层。

设计要点：**取数要么带回完整溯源，要么带回完整的失败详情，没有第三种返回。**
这样「失败必须透明」就不是一句口号——引擎拿不到 Ok 就只能生成带三问的无法验证证据，
不存在「悄悄跳过」这条代码路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Optional, Protocol, TypeVar, runtime_checkable

from ..schemas import Provenance, UnverifiableCategory, UnverifiableDetail

T = TypeVar("T")


@dataclass
class Ok(Generic[T]):
    """成功取数。必带 provenance，否则数字无法回到原始返回。"""

    value: T
    provenance: Provenance
    ok: bool = field(default=True, init=False)


@dataclass
class Err:
    """失败取数。必带三问详情，不允许只回一个 None。"""

    detail: UnverifiableDetail
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    ok: bool = field(default=False, init=False)


FetchResult = Ok[T] | Err


# --- 上游错误码 → 无法验证的原因分类 ---------------------------------------
# 契约见 references/api.md 错误处理表
CODE_MAP: dict[int, UnverifiableCategory] = {
    2001: UnverifiableCategory.PERMISSION_DENIED,
    2003: UnverifiableCategory.PERMISSION_DENIED,
    3001: UnverifiableCategory.DATA_NOT_EXIST,
    3002: UnverifiableCategory.PERIOD_NOT_DUE,
    3004: UnverifiableCategory.CALIBER_MISMATCH,
    1001: UnverifiableCategory.CALIBER_MISMATCH,
    1002: UnverifiableCategory.CALIBER_MISMATCH,
    1003: UnverifiableCategory.CALIBER_MISMATCH,
    1004: UnverifiableCategory.CALIBER_MISMATCH,
    4001: UnverifiableCategory.SOURCE_UNREACHABLE,
    5001: UnverifiableCategory.SOURCE_UNREACHABLE,
    5002: UnverifiableCategory.SOURCE_UNREACHABLE,
    5003: UnverifiableCategory.SOURCE_UNREACHABLE,
}

_CODE_HINT: dict[int, str] = {
    2001: "未认证：X-api-key 缺失或格式错误",
    2003: "无权限或 Key 无效：需前往 API Key 管理页检查授权或重新签发",
    3001: "标的不存在：需先通过元信息接口消歧并核对 thscode",
    3002: "数据尚未准备：保留 request_id 稍后再查，不得补零或使用模拟数据",
    3004: "目标类型不支持该能力",
    4001: "限流：需指数退避，最多重试 3 次",
    5002: "上游数据源超时",
    5003: "上游数据源返回异常或非成功状态",
}


def failure_from_code(
    code: int,
    message: str,
    request_id: Optional[str],
    endpoint: str,
    what_is_needed: str,
    where_to_get: str,
) -> Err:
    """把上游业务错误码翻译成带三问的失败对象。"""
    category = CODE_MAP.get(code, UnverifiableCategory.SOURCE_UNREACHABLE)
    hint = _CODE_HINT.get(code, message)
    return Err(
        detail=UnverifiableDetail(
            category=category,
            what_is_needed=what_is_needed,
            where_to_get=where_to_get,
            failure_evidence=f"上游返回 code={code}（{hint}）；message={message!r}；request_id={request_id}",
        ),
        endpoint=endpoint,
    )


@runtime_checkable
class DataProvider(Protocol):
    """数据源协议。扶摇 / fixture / 未来接入的 iFinD MCP 都实现这一套。"""

    name: str

    def search_ticker(self, query: str) -> FetchResult[dict]: ...

    def price_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]: ...

    def price_historical(
        self, thscode: str, start_ms: int, end_ms: int, adjust: str = "forward"
    ) -> FetchResult[list[dict]]: ...

    def valuation_snapshot(self, thscodes: list[str]) -> FetchResult[list[dict]]: ...

    def income_statements(
        self, thscode: str, period: str, limit: int
    ) -> FetchResult[list[dict]]: ...

    def balance_sheets(
        self, thscode: str, period: str, limit: int
    ) -> FetchResult[list[dict]]: ...

    def cash_flow_statements(
        self, thscode: str, period: str, limit: int
    ) -> FetchResult[list[dict]]: ...

    def financial_indicators(self, thscode: str, report: str) -> FetchResult[dict]: ...

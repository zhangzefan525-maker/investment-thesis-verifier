"""测试共用装置。

## 两条硬规则

1. **完全离线**。所有测试只用 `FixtureProvider`（读 `backend/fixtures/` 下已捕获的真实
   返回快照），不发任何网络请求、不需要任何凭据。`offline_env` 会主动清掉可能存在的
   `ANTHROPIC_API_KEY` / `HITHINK_FINANCE_API_KEY`，避免解析层被 LLM 分支接管或取数层
   被真实接口接管——一旦接管，「离线」就只是口头承诺。
2. **不猜数字**。断言里出现的每一个数值都来自实跑结果，不是照抄产品文档。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

FIXTURES_ROOT = BACKEND_ROOT / "fixtures"

# 已捕获的 4 个标的
CAPTURED_THSCODES = ("002594.SZ", "300750.SZ", "600519.SH", "601318.SH")


# --------------------------------------------------------------------------
# 笔试要求的四类命题各一例（也是本次交付实跑过的四例）
# --------------------------------------------------------------------------

CASES: dict[str, str] = {
    "maotai_divergence": "我认为贵州茅台估值已经回落但基本面并没有恶化",
    "pingan_divergence": "我认为中国平安估值已经回落但基本面并没有恶化",
    "catl_attribution": "我认为宁德时代盈利改善来自主营业务",
    "catl_transmission": "我认为碳酸锂价格上涨会挤压宁德时代的利润空间",
}

# 每个案例的期望结论（实跑确认，非文档抄写）
EXPECTED = {
    "maotai_divergence": {"ticker": "600519", "thscode": "600519.SH", "name": "贵州茅台", "verdict": "refute"},
    "pingan_divergence": {"ticker": "601318", "thscode": "601318.SH", "name": "中国平安", "verdict": "support"},
    "catl_attribution": {"ticker": "300750", "thscode": "300750.SZ", "name": "宁德时代", "verdict": "refute"},
    "catl_transmission": {"ticker": "300750", "thscode": "300750.SZ", "name": "宁德时代", "verdict": "unverifiable"},
}

OFFLINE_NOTE = "pytest 离线快照模式（仅供测试断言，非产品运行数据）"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
    """任何测试试图发起 TCP 连接都会立刻失败。

    「完全离线」由代码保证，不靠自觉：只要有人不小心把 provider 换成真实客户端，
    这个装置会当场把测试打红，而不是让它悄悄跑出一次真实请求。
    """
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("测试禁止联网：检测到 socket 连接尝试")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    yield


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch):
    """保证每个测试都在无凭据、无网络的条件下运行。"""
    for var in ("ANTHROPIC_API_KEY", "HITHINK_FINANCE_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from app.engine.parse import llm_available

    assert not llm_available(), "被测环境里仍有 LLM 凭据，解析层会走网络分支，测试不再是离线的"
    yield


def run_offline(text: str):
    """跑一次完整的离线验证链路。"""
    from app.engine.run import run_verification
    from app.providers.fixture import FixtureProvider

    return run_verification(
        raw_text=text,
        provider=FixtureProvider(),
        data_mode="fixture",
        data_mode_note=OFFLINE_NOTE,
    )


@pytest.fixture(scope="session")
def offline_runs() -> dict:
    """四个预设命题各跑一次，整个测试会话共享结果（避免重复跑同一条链路）。"""
    # session 级装置先于 function 级 autouse 装置建立，所以这里自己清一遍环境变量
    for var in ("ANTHROPIC_API_KEY", "HITHINK_FINANCE_API_KEY"):
        os.environ.pop(var, None)
    return {cid: run_offline(text) for cid, text in CASES.items()}


@pytest.fixture
def provider():
    from app.providers.fixture import FixtureProvider

    return FixtureProvider()


def evidence_by_sq(thesis_verification, sq_id: str):
    """按子问题 ID 取证据；取不到直接失败，而不是返回 None 让断言静默通过。"""
    hits = [e for e in thesis_verification.evidence if e.sub_question_id == sq_id]
    assert len(hits) == 1, f"子问题 {sq_id} 的证据条数为 {len(hits)}，期望恰好 1 条"
    return hits[0]


def evidence_by_id(thesis_verification, evidence_id: str):
    hits = [e for e in thesis_verification.evidence if e.id == evidence_id]
    assert len(hits) == 1, f"证据 {evidence_id} 的条数为 {len(hits)}，期望恰好 1 条"
    return hits[0]

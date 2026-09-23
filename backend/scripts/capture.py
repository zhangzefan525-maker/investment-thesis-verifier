"""把扶摇的真实返回捕获成 fixture。

用途有两个：测试不依赖网络，演示当天数值固定。
硬约束：**fixture 只能来自真实调用**，不得手工编造。每份都写入
`_request_id`、`_captured_at`、`_endpoint`、`_params`，保证可回溯。

用法：
    python scripts/capture.py 600519.SH 000858.SZ
    python scripts/capture.py --list
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import Err, Ok  # noqa: E402
from app.providers.fuyao import FuyaoClient, credential_status  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
MAX_LOOKBACK_YEARS = 9.5
HISTORY_LIMIT = 12


def save(thscode: str, key: str, result, extra: dict | None = None) -> bool:
    d = ROOT / thscode.replace(".", "_")
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(result, Err):
        print(f"  [FAIL] {key}: {result.detail.failure_evidence[:150]}")
        return False
    assert isinstance(result, Ok)
    payload = {
        "_captured_at": datetime.now(timezone.utc).isoformat(),
        "_endpoint": result.provenance.endpoint,
        "_params": result.provenance.request_params,
        "_request_id": result.provenance.request_id,
        "_note": "本文件由真实调用扶摇接口捕获，非模拟数据",
        "data": result.value,
    }
    if extra:
        payload.update(extra)
    path = d / f"{key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(result.value) if isinstance(result.value, list) else (
        len(result.value) if isinstance(result.value, dict) else 1
    )
    print(f"  [ OK ] {key}: {n} 条 → {path.relative_to(ROOT.parent)}")
    return True


def capture(thscode: str) -> int:
    c = FuyaoClient()
    print(f"\n=== 捕获 {thscode} ===")
    ok = 0

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=int(365.25 * MAX_LOOKBACK_YEARS))).timestamp() * 1000)

    plan = [
        ("search_ticker", lambda: c.search_ticker(thscode)),
        ("price_snapshot", lambda: c.price_snapshot([thscode])),
        ("prices_historical", lambda: c.price_historical(thscode, start_ms, end_ms, "forward")),
        ("prices_historical_none", lambda: c.price_historical(thscode, start_ms, end_ms, "none")),
        ("valuations_snapshot", lambda: c.valuation_snapshot([thscode])),
        ("income_statements", lambda: c.income_statements(thscode, "quarterly", HISTORY_LIMIT)),
        ("income_statements_annual", lambda: c.income_statements(thscode, "annual", HISTORY_LIMIT)),
        ("balance_sheets", lambda: c.balance_sheets(thscode, "annual", HISTORY_LIMIT)),
        ("cash_flow_statements", lambda: c.cash_flow_statements(thscode, "annual", HISTORY_LIMIT)),
        ("cash_flow_statements_q", lambda: c.cash_flow_statements(thscode, "quarterly", HISTORY_LIMIT)),
    ]

    # 财务指标的报告期标签依赖季报，所以放在最后
    inc = c.income_statements(thscode, "quarterly", HISTORY_LIMIT)
    report_label = ""
    if isinstance(inc, Ok) and inc.value:
        lat = max(inc.value, key=lambda r: r.get("period_end_ms") or 0)
        q = {"Q1": "1", "Q2": "2", "Q3": "3", "Q4": "4"}.get(lat.get("fiscal_period", ""))
        if q:
            report_label = f"{lat['fiscal_year']}-{q}"
    if report_label:
        plan.append(
            ("financial_indicators", lambda: c.financial_indicators(thscode, report_label))
        )

    for key, fn in plan:
        try:
            if save(thscode, key, fn()):
                ok += 1
        except Exception as exc:  # 捕获过程本身的异常也要留痕
            print(f"  [FAIL] {key}: {type(exc).__name__}: {exc}")

    print(f"--- {thscode}: {ok}/{len(plan)} 路成功 ---")
    return ok


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--list" in sys.argv:
        if not ROOT.is_dir():
            print("尚无任何 fixture")
            return
        for d in sorted(ROOT.iterdir()):
            if d.is_dir():
                print(f"{d.name}:")
                for f in sorted(d.glob("*.json")):
                    print(f"  - {f.stem}")
        return

    status = credential_status()
    print(f"凭据状态: {'已找到' if status['present'] else '未找到'} ({status['origin']})")
    if not status["present"]:
        print("未找到扶摇凭据，无法捕获真实数据。")
        print("获取方式：https://fuyao.aicubes.cn/admin/ 扫码登录后创建 API Key（免费），")
        print("然后设置环境变量 HITHINK_FINANCE_API_KEY，或写入")
        print(r"  %APPDATA%\hithink-finance\credentials.env")
        sys.exit(1)

    if not args:
        print("用法: python scripts/capture.py <thscode> [<thscode> ...]")
        sys.exit(2)

    total = sum(capture(c) for c in args)
    print(f"\n总计捕获 {total} 路数据 → {ROOT}")


if __name__ == "__main__":
    main()

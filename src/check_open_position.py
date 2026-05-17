#!/usr/bin/env python3
"""List open US stock and option positions from moomoo OpenD."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from opend_env import prepare_opend

US_OPTION_CODE_RE = re.compile(r"^US\.[A-Z]+\d{6}[CP]\d+$")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if hasattr(value, "item"):
        value = value.item()
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_get(row: Any, key: str, default: Any = "") -> Any:
    if hasattr(row, "get"):
        val = row.get(key)
    else:
        val = getattr(row, key, None)
    return default if val is None else val


def _parse_trd_env(name: str | None):
    from moomoo import TrdEnv

    if name and name.strip().upper() == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE


def _parse_security_firm(name: str | None):
    from moomoo import SecurityFirm

    if not name:
        return SecurityFirm.NONE
    key = name.strip().upper()
    if hasattr(SecurityFirm, key):
        return getattr(SecurityFirm, key)
    raise SystemExit(f"Unknown security firm: {name}")


def _market_auth_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).upper() for item in value]
    text = str(value).strip()
    if not text or text == "N/A":
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip().strip("'\"").upper() for part in text.split(",") if part.strip()]


def is_us_option(code: str) -> bool:
    return bool(US_OPTION_CODE_RE.match(code))


def is_us_stock(code: str) -> bool:
    return code.startswith("US.") and not is_us_option(code)


def _create_trade_context(settings, security_firm):
    from moomoo import OpenSecTradeContext, TrdMarket

    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "filter_trdmarket": TrdMarket.US,
        "security_firm": security_firm,
    }
    try:
        import inspect
        from moomoo import OpenSecTradeContext as _ctx_cls

        if "ai_type" in inspect.signature(_ctx_cls.__init__).parameters:
            kwargs["ai_type"] = 1
    except (ImportError, TypeError, ValueError):
        pass

    encrypt = settings.enable_encrypt
    kwargs["is_encrypt"] = encrypt if encrypt else False
    return OpenSecTradeContext(**kwargs)


def _select_account(accs, trd_env, acc_id: int | None):
    from moomoo import TrdEnv

    env_name = "REAL" if trd_env == TrdEnv.REAL else "SIMULATE"
    rows = []
    for index in range(len(accs)):
        row = accs.iloc[index]
        if str(_safe_get(row, "trd_env", "")).upper() != env_name:
            continue
        auth = _market_auth_list(_safe_get(row, "trdmarket_auth", default=[]))
        if "US" not in auth:
            continue
        role = str(_safe_get(row, "acc_role", "")).upper()
        if trd_env == TrdEnv.REAL and role == "MASTER":
            continue
        rows.append(row)

    if not rows:
        raise SystemExit(f"No US-capable account found for trd_env={env_name}")

    if acc_id is not None:
        for row in rows:
            if int(_safe_get(row, "acc_id", 0)) == acc_id:
                return int(acc_id), row
        raise SystemExit(f"Account {acc_id} not found for trd_env={env_name}")

    default_acc = os.getenv("FUTU_ACC_ID")
    if default_acc:
        try:
            preferred = int(default_acc)
        except ValueError as exc:
            raise SystemExit(f"Invalid FUTU_ACC_ID: {default_acc}") from exc
        for row in rows:
            if int(_safe_get(row, "acc_id", 0)) == preferred:
                return preferred, row

    chosen = rows[0]
    return int(_safe_get(chosen, "acc_id", 0)), chosen


def _row_to_position(row: Any) -> dict[str, Any]:
    code = str(_safe_get(row, "code", default=""))
    qty = _safe_float(_safe_get(row, "qty", default=0))
    return {
        "code": code,
        "name": str(_safe_get(row, "stock_name", default="")),
        "instrument_type": "option" if is_us_option(code) else "stock",
        "position_side": str(_safe_get(row, "position_side", default="")),
        "qty": qty,
        "can_sell_qty": _safe_float(_safe_get(row, "can_sell_qty", default=0)),
        "average_cost": _safe_float(_safe_get(row, "average_cost", default=0)),
        "nominal_price": _safe_float(_safe_get(row, "nominal_price", default=0)),
        "market_val": _safe_float(_safe_get(row, "market_val", default=0)),
        "unrealized_pl": _safe_float(_safe_get(row, "unrealized_pl", default=0)),
        "pl_ratio_avg_cost": _safe_float(_safe_get(row, "pl_ratio_avg_cost", default=0)),
        "realized_pl": _safe_float(_safe_get(row, "realized_pl", default=0)),
        "today_pl_val": _safe_float(_safe_get(row, "today_pl_val", default=0)),
        "currency": str(_safe_get(row, "currency", default="USD")),
        "position_id": str(_safe_get(row, "position_id", default="")),
    }


def fetch_open_positions(
    trd_env_name: str | None,
    acc_id: int | None,
    security_firm_name: str | None,
) -> dict[str, Any]:
    from moomoo import RET_OK

    settings = prepare_opend()
    trd_env = _parse_trd_env(
        trd_env_name or os.getenv("FUTU_TRD_ENV", "SIMULATE"),
    )
    security_firm = _parse_security_firm(
        security_firm_name or os.getenv("FUTU_SECURITY_FIRM"),
    )

    ctx = None
    try:
        ctx = _create_trade_context(settings, security_firm)
        ret, accs = ctx.get_acc_list()
        if ret != RET_OK:
            raise SystemExit(f"Failed to list accounts: {accs}")

        selected_acc_id, account_row = _select_account(accs, trd_env, acc_id)
        ret, pos_data = ctx.position_list_query(
            trd_env=trd_env,
            acc_id=selected_acc_id,
            refresh_cache=True,
        )
        if ret != RET_OK:
            raise SystemExit(f"Failed to query positions: {pos_data}")

        stocks: list[dict[str, Any]] = []
        options: list[dict[str, Any]] = []
        if pos_data is not None and len(pos_data) > 0:
            for index in range(len(pos_data)):
                row = pos_data.iloc[index]
                code = str(_safe_get(row, "code", default=""))
                market = str(_safe_get(row, "position_market", default="")).upper()
                qty = _safe_float(_safe_get(row, "qty", default=0))
                if qty == 0:
                    continue
                if market != "US" and not code.startswith("US."):
                    continue

                position = _row_to_position(row)
                if position["instrument_type"] == "option":
                    options.append(position)
                else:
                    stocks.append(position)

        env_label = "REAL" if str(_safe_get(account_row, "trd_env", "")).upper() == "REAL" else "SIMULATE"
        return {
            "trd_env": env_label,
            "acc_id": selected_acc_id,
            "acc_type": str(_safe_get(account_row, "acc_type", default="")),
            "uni_card_num": str(_safe_get(account_row, "uni_card_num", default="")),
            "security_firm": str(_safe_get(account_row, "security_firm", default="")),
            "stocks": stocks,
            "options": options,
            "summary": {
                "stock_count": len(stocks),
                "option_count": len(options),
                "total_market_val": round(
                    sum(item["market_val"] for item in stocks + options),
                    2,
                ),
                "total_unrealized_pl": round(
                    sum(item["unrealized_pl"] for item in stocks + options),
                    2,
                ),
            },
        }
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _print_table(title: str, positions: list[dict[str, Any]]) -> None:
    print(f"\n{title} ({len(positions)})")
    print("=" * 96)
    if not positions:
        print("  No open positions")
        return

    print(
        f"  {'Code':<22} {'Name':<18} {'Side':<6} {'Qty':>8} "
        f"{'Avg Cost':>10} {'Price':>10} {'Mkt Val':>12} {'P/L%':>8}"
    )
    print("  " + "-" * 94)
    for item in positions:
        print(
            f"  {item['code']:<22} {item['name'][:18]:<18} "
            f"{item['position_side']:<6} {item['qty']:>8.2f} "
            f"{item['average_cost']:>10.2f} {item['nominal_price']:>10.2f} "
            f"{item['market_val']:>12.2f} {item['pl_ratio_avg_cost']:>8.2f}%"
        )


def _print_report(payload: dict[str, Any]) -> None:
    print("US open positions")
    print("=" * 96)
    print(
        f"  Environment : {payload['trd_env']}\n"
        f"  Account ID  : {payload['acc_id']}\n"
        f"  Account type: {payload['acc_type']}\n"
        f"  Card number : {payload.get('uni_card_num') or 'N/A'}\n"
        f"  Broker      : {payload.get('security_firm') or 'N/A'}"
    )
    print(
        f"\n  Total market value : {payload['summary']['total_market_val']:.2f}\n"
        f"  Total unrealized P/L: {payload['summary']['total_unrealized_pl']:.2f}"
    )
    _print_table("US stocks", payload["stocks"])
    _print_table("US options", payload["options"])
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check open US stock and option positions via moomoo OpenD.",
    )
    parser.add_argument(
        "--trd-env",
        choices=["REAL", "SIMULATE"],
        default=None,
        help="Trading environment (default: SIMULATE, or FUTU_TRD_ENV from .env)",
    )
    parser.add_argument("--acc-id", type=int, default=None, help="Account ID")
    parser.add_argument(
        "--security-firm",
        choices=[
            "FUTUSECURITIES",
            "FUTUINC",
            "FUTUSG",
            "FUTUAU",
            "FUTUCA",
            "FUTUJP",
            "FUTUMY",
            "NONE",
        ],
        default=None,
        help="Broker identifier (default: FUTU_SECURITY_FIRM or NONE)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    payload = fetch_open_positions(
        trd_env_name=args.trd_env,
        acc_id=args.acc_id,
        security_firm_name=args.security_firm,
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(payload)


if __name__ == "__main__":
    main()

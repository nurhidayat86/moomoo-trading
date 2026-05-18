#!/usr/bin/env python3
"""Query moomoo quote permissions and subscription quota limits via OpenD."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from opend_env import configure_encryption, ensure_sdk, load_dotenv, load_settings

QOT_RIGHT_DESC = {
    "N/A": "Unknown",
    "NO": "No permission",
    "BMP": "BMP (Basic Summary)",
    "LV1": "LV1",
    "LV2": "LV2",
    "LV3": "LV3",
    "SF": "SF (Advanced Quote Enabled)",
}

QOT_RIGHT_FIELDS = {
    "hk_qot_right": "HK Stocks",
    "us_qot_right": "US Stocks",
    "cn_qot_right": "A-Shares",
    "hk_option_qot_right": "HK Options",
    "hk_future_qot_right": "HK Futures",
    "us_option_qot_right": "US Options",
    "us_future_qot_right": "US Futures",
    "sg_future_qot_right": "SG Futures",
    "jp_future_qot_right": "JP Futures",
}


from quote_context import create_quote_context as _create_quote_context


def _normalize_user_info(data: Any) -> dict[str, Any]:
    if hasattr(data, "to_dict"):
        return data.to_dict()
    if isinstance(data, dict):
        return dict(data)
    return {str(key): data[key] for key in data.keys()}


def _normalize_subscription(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "total_used": 0,
            "remain": 0,
            "own_used": 0,
            "subscriptions": {},
        }

    sub_list = data.get("sub_list", {})
    subscriptions: dict[str, list[str]] = {}
    if isinstance(sub_list, dict):
        for key, codes in sub_list.items():
            name = str(key).split(".")[-1] if hasattr(key, "name") else str(key)
            subscriptions[name] = list(codes) if isinstance(codes, list) else [codes]

    return {
        "total_used": int(data.get("total_used", 0) or 0),
        "remain": int(data.get("remain", 0) or 0),
        "own_used": int(data.get("own_used", 0) or 0),
        "subscriptions": subscriptions,
    }


def _normalize_history_kl_quota(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, tuple) and len(data) >= 2:
        result: dict[str, Any] = {
            "used_quota": data[0],
            "remain_quota": data[1],
        }
        if len(data) > 2:
            result["detail_list"] = data[2]
        return result
    if hasattr(data, "iloc") and len(data) > 0:
        row = data.iloc[0]
        if hasattr(row, "to_dict"):
            return row.to_dict()
        return {col: row[col] for col in data.columns}
    if isinstance(data, dict):
        return dict(data)
    return {"raw": str(data)}


def _quote_permissions(user_info: dict[str, Any]) -> dict[str, str]:
    permissions: dict[str, str] = {}
    for field, label in QOT_RIGHT_FIELDS.items():
        level = str(user_info.get(field, "N/A"))
        permissions[label] = QOT_RIGHT_DESC.get(level, level)
    return permissions


def fetch_user_info(*, all_connections: bool = True) -> dict[str, Any]:
    from moomoo import RET_OK

    load_dotenv()
    settings = load_settings()
    sdk_version = ensure_sdk()
    configure_encryption(settings)

    quote_ctx = None
    try:
        quote_ctx = _create_quote_context(settings)

        ret, user_raw = quote_ctx.get_user_info()
        if ret != RET_OK:
            raise RuntimeError(f"get_user_info failed: {user_raw}")

        ret, sub_raw = quote_ctx.query_subscription(is_all_conn=all_connections)
        if ret != RET_OK:
            raise RuntimeError(f"query_subscription failed: {sub_raw}")

        ret, kl_raw = quote_ctx.get_history_kl_quota(get_detail=False)
        if ret != RET_OK:
            raise RuntimeError(f"get_history_kl_quota failed: {kl_raw}")

        user_info = _normalize_user_info(user_raw)
        subscription = _normalize_subscription(sub_raw)
        history_kl = _normalize_history_kl_quota(kl_raw)

        sub_quota = int(user_info.get("sub_quota", 0) or 0)
        history_kl_quota = int(user_info.get("history_kl_quota", 0) or 0)

        return {
            "host": settings.host,
            "port": settings.port,
            "sdk_version": sdk_version,
            "user": {
                "nick_name": user_info.get("nick_name"),
                "user_id": user_info.get("user_id"),
                "user_attr": user_info.get("user_attr"),
            },
            "quota_limits": {
                "subscription_quota": sub_quota,
                "history_kline_quota": history_kl_quota,
            },
            "subscription_usage": {
                "total_used": subscription["total_used"],
                "remain": subscription["remain"],
                "own_used": subscription["own_used"],
                "subscription_quota": sub_quota,
            },
            "history_kline_usage": history_kl,
            "quote_permissions": _quote_permissions(user_info),
            "active_subscriptions": subscription["subscriptions"],
            "all_connections": all_connections,
        }
    finally:
        if quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass


def _print_human(payload: dict[str, Any]) -> None:
    limits = payload["quota_limits"]
    usage = payload["subscription_usage"]
    hist = payload.get("history_kline_usage") or {}

    print("=" * 60)
    print("Moomoo user info & subscription limits")
    print("=" * 60)
    user = payload["user"]
    print(f"  Nickname:  {user.get('nick_name', 'N/A')}")
    print(f"  User ID:   {user.get('user_id', 'N/A')}")
    print(f"  Attribute: {user.get('user_attr', 'N/A')}")
    print()
    print("  Subscription quota (real-time subscribe):")
    print(f"    Tier limit:  {limits['subscription_quota']}")
    print(f"    Used (all):  {usage['total_used']}")
    print(f"    Remaining:   {usage['remain']}")
    print(f"    This conn:   {usage['own_used']}")
    print()
    print("  Historical kline quota (request_history_kline):")
    print(f"    Tier limit:  {limits['history_kline_quota']}")
    if hist:
        used = hist.get("used_quota", hist.get("used", "N/A"))
        remain = hist.get("remain_quota", hist.get("remain", "N/A"))
        print(f"    Used:        {used}")
        print(f"    Remaining:   {remain}")
    print()
    print("  Quote permissions:")
    for label, level in payload["quote_permissions"].items():
        print(f"    {label:<14} {level}")

    subs = payload.get("active_subscriptions") or {}
    scope = "all connections" if payload.get("all_connections") else "this connection"
    print()
    print(f"  Active subscriptions ({scope}):")
    if subs:
        for subtype, codes in subs.items():
            print(f"    {subtype}:")
            for code in codes:
                print(f"      - {code}")
    else:
        print("    (none)")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Get moomoo quote permissions and subscription quota limits (reads .env).",
    )
    parser.add_argument(
        "--current",
        action="store_true",
        help="Subscription usage for this connection only (default: all connections).",
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON")
    args = parser.parse_args()

    try:
        payload = fetch_user_info(all_connections=not args.current)
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(payload)


if __name__ == "__main__":
    main()

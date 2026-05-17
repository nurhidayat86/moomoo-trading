#!/usr/bin/env python3
"""Verify connectivity to moomoo OpenD using settings from .env."""

from __future__ import annotations

import argparse
import json
import socket
import sys

from opend_env import configure_encryption, ensure_sdk, load_dotenv, load_settings


def _check_socket(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True, f"TCP reachable at {host}:{port}"
    except OSError as exc:
        return False, f"Cannot reach OpenD at {host}:{port}: {exc}"
    finally:
        sock.close()


def _check_api(settings, use_encrypt: bool) -> tuple[bool, dict]:
    from moomoo import RET_OK, OpenQuoteContext

    quote_ctx = None
    try:
        quote_ctx = OpenQuoteContext(
            host=settings.host,
            port=settings.port,
            is_encrypt=use_encrypt if use_encrypt else False,
        )
        ret, data = quote_ctx.get_global_state()
        if ret != RET_OK:
            return False, {"error": str(data)}

        if hasattr(data, "to_dict"):
            state = data.to_dict()
        elif isinstance(data, dict):
            state = data
        else:
            state = {str(key): data[key] for key in data.keys()}

        return True, state
    except Exception as exc:
        return False, {"error": str(exc)}
    finally:
        if quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass


def run_check(output_json: bool = False) -> int:
    load_dotenv()
    settings = load_settings()
    sdk_version = ensure_sdk()

    checks: list[dict] = []

    socket_ok, socket_msg = _check_socket(settings.host, settings.port)
    checks.append({"check": "tcp", "ok": socket_ok, "message": socket_msg})
    if not socket_ok:
        return _emit_result(False, settings, sdk_version, checks, None, output_json)

    try:
        encrypt_enabled = configure_encryption(settings)
        encrypt_msg = (
            f"Protocol encryption enabled ({settings.rsa_private_key_path})"
            if encrypt_enabled
            else "Protocol encryption disabled (OPEND_ENABLE_ENCRYPT=false)"
        )
    except SystemExit as exc:
        checks.append({"check": "rsa_key", "ok": False, "message": str(exc)})
        return _emit_result(False, settings, sdk_version, checks, None, output_json)

    checks.append({"check": "rsa_key", "ok": True, "message": encrypt_msg})

    api_ok, api_payload = _check_api(settings, encrypt_enabled)
    checks.append(
        {
            "check": "api",
            "ok": api_ok,
            "message": "OpenQuoteContext connected" if api_ok else str(api_payload.get("error")),
        }
    )

    return _emit_result(
        api_ok,
        settings,
        sdk_version,
        checks,
        api_payload if api_ok else None,
        output_json,
    )


def _emit_result(ok, settings, sdk_version, checks, global_state, output_json) -> int:
    payload = {
        "ok": ok,
        "host": settings.host,
        "port": settings.port,
        "rsa_private_key_path": str(settings.rsa_private_key_path),
        "sdk_version": sdk_version,
        "checks": checks,
        "global_state": global_state,
    }

    if output_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for item in checks:
            mark = "OK" if item["ok"] else "FAIL"
            print(f"[{mark}] {item['check']}: {item['message']}")
        if global_state:
            print("\nOpenD global state:")
            for key, value in global_state.items():
                print(f"  {key}: {value}")
        print("\nConnection check passed." if ok else "\nConnection check failed.")

    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check connectivity to moomoo OpenD (reads .env in project root).",
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON")
    args = parser.parse_args()
    raise SystemExit(run_check(output_json=args.json))


if __name__ == "__main__":
    main()

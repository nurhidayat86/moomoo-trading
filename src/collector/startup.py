"""Pre-flight checks before subscribe."""

from __future__ import annotations

import socket

from config_loader import AppConfig, check_subscription_quota
from opend_env import OpenDSettings, ensure_sdk


def probe_tcp(host: str, port: int, timeout: float = 3.0) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def probe_opend(settings: OpenDSettings) -> None:
    from moomoo import RET_OK

    from quote_context import create_quote_context

    probe_tcp(settings.host, settings.port)
    ctx = create_quote_context(settings)
    try:
        ret, state = ctx.get_global_state()
        if ret != RET_OK:
            raise RuntimeError(f"get_global_state failed: {state}")
    finally:
        ctx.close()


def run_startup_checks(
    settings: OpenDSettings,
    config: AppConfig,
    *,
    dry_run: bool,
) -> dict[str, int] | None:
    ensure_sdk()
    probe_opend(settings)

    from quote_context import create_quote_context

    ctx = create_quote_context(settings)
    try:
        quota = check_subscription_quota(ctx, config)
    finally:
        ctx.close()

    if dry_run:
        return quota
    return quota

"""Shared OpenQuoteContext factory for moomoo scripts."""

from __future__ import annotations

from typing import Any

from opend_env import OpenDSettings


def create_quote_context(settings: OpenDSettings):
    from moomoo import OpenQuoteContext

    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
    }
    try:
        import inspect

        if "ai_type" in inspect.signature(OpenQuoteContext.__init__).parameters:
            kwargs["ai_type"] = 1
    except (TypeError, ValueError):
        pass

    encrypt = settings.enable_encrypt
    kwargs["is_encrypt"] = encrypt if encrypt else False
    return OpenQuoteContext(**kwargs)

"""Shared OpenD connection settings loaded from .env."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_SDK_VERSION = "10.4.6408"


@dataclass(frozen=True)
class OpenDSettings:
    host: str
    port: int
    rsa_private_key_path: Path
    enable_encrypt: bool


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _load
    except ImportError as exc:
        print(
            "python-dotenv is not installed. Run: pip install python-dotenv",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        _load(env_path)
    else:
        _load()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> OpenDSettings:
    host = os.getenv("OPEND_HOST") or os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port_raw = os.getenv("OPEND_PORT") or os.getenv("FUTU_OPEND_PORT", "11111")
    rsa_path_raw = (
        os.getenv("RSA_PRIVATE_KEY_PATH")
        or os.getenv("OPEND_RSA_PRIVATE_KEY_PATH")
        or "~/.ssh/rsa_private_key.pem"
    )

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid OPEND_PORT value: {port_raw}") from exc

    return OpenDSettings(
        host=host.strip(),
        port=port,
        rsa_private_key_path=Path(os.path.expanduser(rsa_path_raw)).resolve(),
        enable_encrypt=_env_bool("OPEND_ENABLE_ENCRYPT", True),
    )


def _parse_version(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except ValueError:
        return (0,)


def ensure_sdk() -> str:
    try:
        import moomoo
    except ImportError as exc:
        raise SystemExit(
            "moomoo-api is not installed. Run: pip install \"moomoo-api>=10.4.6408\""
        ) from exc

    version = getattr(moomoo, "__version__", "0")
    if _parse_version(version) < _parse_version(MIN_SDK_VERSION):
        raise SystemExit(
            f"moomoo-api {version} is too old; require >= {MIN_SDK_VERSION}"
        )
    return version


def configure_encryption(settings: OpenDSettings) -> bool:
    if not settings.enable_encrypt:
        return False

    if not settings.rsa_private_key_path.is_file():
        raise SystemExit(
            "RSA private key not found: "
            f"{settings.rsa_private_key_path}\n"
            "Set RSA_PRIVATE_KEY_PATH in .env"
        )

    from moomoo import SysConfig

    SysConfig.enable_proto_encrypt(True)
    SysConfig.set_init_rsa_file(str(settings.rsa_private_key_path))
    return True


def prepare_opend() -> OpenDSettings:
    load_dotenv()
    settings = load_settings()
    ensure_sdk()
    configure_encryption(settings)
    return settings

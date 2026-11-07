"""One ``settings`` object, loaded from ``.env`` (python-dotenv) and the process environment.

Rules:
- Secrets are only read here. Never print, log or persist them.
- ``repr(settings)`` / ``str(settings)`` redact every secret, so an accidental print is safe.
- Real environment variables win over ``.env`` (handy for tests and CI).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Fields whose values must never be shown. URLs that may embed credentials count as secrets.
SECRET_FIELDS = frozenset({
    "yext_api_key",
    "yext_sandbox_key",
    "openai_api_key",
    "anthropic_foundry_api_key",
    "link_secret",
    "whatsapp_token",
    "smtp_url",
    "mongodb_uri",
})


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    root: Path
    dataset_dir: Path
    # Yext: the mock (default) and the sandbox
    yext_base_url: str
    yext_api_key: str
    yext_sandbox_url: str
    yext_sandbox_key: str
    yext_sandbox_entity_id: str
    yext_api_version: str
    # LLMs (optional)
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    anthropic_foundry_base_url: str
    anthropic_foundry_api_key: str
    claude_model: str
    claude_fast_model: str
    # Signed one-tap links
    link_secret: str
    public_base_url: str
    # Channels (optional)
    whatsapp_token: str
    whatsapp_phone_number_id: str
    smtp_url: str
    # MongoDB
    mongodb_uri: str
    mongodb_db: str
    # Suggestion engine: "baseline" until the ML engine exists, then "ml"
    engine: str

    def has(self, name: str) -> bool:
        """True if the setting is non-empty. Use this instead of reading a secret to test for it."""
        return bool(getattr(self, name))

    def _redacted(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name in SECRET_FIELDS:
                out[f.name] = "***" if v else ""
            else:
                out[f.name] = str(v) if isinstance(v, Path) else v
        return out

    def __repr__(self) -> str:  # never leaks secrets
        body = ", ".join(f"{k}={v!r}" for k, v in self._redacted().items())
        return f"Settings({body})"

    __str__ = __repr__


def load_settings(env_file: Path | None = None) -> Settings:
    """Read ``.env`` (without overriding real environment variables) and build ``Settings``."""
    load_dotenv(env_file or ROOT / ".env", override=False)
    dataset = Path(_env("DATASET_DIR", "dataset/FONECTA_CONNECT_SUGGESTS_2026"))
    if not dataset.is_absolute():
        dataset = ROOT / dataset
    return Settings(
        root=ROOT,
        dataset_dir=dataset,
        yext_base_url=_env("YEXT_BASE_URL", "http://127.0.0.1:8787"),
        yext_api_key=_env("YEXT_API_KEY", "demo"),
        yext_sandbox_url=_env("YEXT_SANDBOX_URL", "https://sbx-api.yextapis.com"),
        yext_sandbox_key=_env("YEXT_SANDBOX_KEY"),
        yext_sandbox_entity_id=_env("YEXT_SANDBOX_ENTITY_ID"),
        yext_api_version=_env("YEXT_API_VERSION", "20261106"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL"),
        openai_model=_env("OPENAI_MODEL"),
        anthropic_foundry_base_url=_env("ANTHROPIC_FOUNDRY_BASE_URL"),
        anthropic_foundry_api_key=_env("ANTHROPIC_FOUNDRY_API_KEY"),
        claude_model=_env("CLAUDE_MODEL"),
        claude_fast_model=_env("CLAUDE_FAST_MODEL"),
        link_secret=_env("LINK_SECRET"),
        public_base_url=_env("PUBLIC_BASE_URL", "http://127.0.0.1:5050"),
        whatsapp_token=_env("WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=_env("WHATSAPP_PHONE_NUMBER_ID"),
        smtp_url=_env("SMTP_URL"),
        mongodb_uri=_env("MONGODB_URI"),
        mongodb_db=_env("MONGODB_DB", "connect_suggests"),
        engine=_env("ENGINE", "baseline").lower(),
    )


settings = load_settings()

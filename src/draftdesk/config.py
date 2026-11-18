"""YAML configuration with ${ENV_VAR} interpolation.

Secrets never live in the YAML file: reference them as ${NAME} and supply them
through the environment (or a .env file loaded by your process manager).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


class StoreConfig(BaseModel):
    name: str = "Our Store"
    signature: str = "Customer Support"
    # Addresses the platform uses when it forwards contact-form submissions.
    # For these, the customer's real address is read from the body or Reply-To.
    form_senders: list[str] = Field(default_factory=list)
    internal_domains: list[str] = Field(default_factory=list)
    ignore_senders: list[str] = Field(default_factory=list)
    ignore_subjects: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    """`provider` picks the adapter; every other key is passed to it untouched."""

    provider: str
    options: dict[str, Any] = Field(default_factory=dict)


class ReviewConfig(BaseModel):
    # dashboard: approve and send from the DraftDesk web UI.
    # mailbox:   save the draft into the mailbox's Drafts folder; the agent
    #            sends it from their normal mail client.
    mode: Literal["dashboard", "mailbox"] = "dashboard"


class KnowledgeConfig(BaseModel):
    paths: list[str] = Field(default_factory=list)
    max_chars: int = 200_000


class Config(BaseModel):
    store: StoreConfig = Field(default_factory=StoreConfig)
    email: ProviderConfig
    commerce: ProviderConfig = ProviderConfig(provider="none")
    llm: ProviderConfig
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    storage_path: str = "data/draftdesk.db"
    poll_interval_seconds: int = 120
    max_orders: int = 3


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):

        def sub(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name} is referenced in config but not set")

        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _provider(raw: dict[str, Any] | None, section: str) -> dict[str, Any]:
    if not raw or "provider" not in raw:
        raise ConfigError(f"config section '{section}' needs a 'provider' key")
    options = {k: v for k, v in raw.items() if k != "provider"}
    return {"provider": raw["provider"], "options": options}


def parse_config(raw: dict[str, Any]) -> Config:
    raw = _interpolate(raw)
    data = dict(raw)
    data["email"] = _provider(raw.get("email"), "email")
    data["llm"] = _provider(raw.get("llm"), "llm")
    if raw.get("commerce"):
        data["commerce"] = _provider(raw.get("commerce"), "commerce")
    return Config.model_validate(data)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def load_knowledge(cfg: KnowledgeConfig, base_dir: Path | None = None) -> str:
    """Concatenate every .md/.txt file under the configured paths.

    The whole knowledge base goes into the system prompt, so it has a hard size
    limit. Exceeding it is an error rather than a silent truncation: a reply
    drafted from half a returns policy is worse than no reply.
    """
    base_dir = base_dir or Path.cwd()
    chunks: list[str] = []
    for entry in cfg.paths:
        root = Path(entry)
        if not root.is_absolute():
            root = base_dir / root
        if not root.exists():
            raise ConfigError(f"knowledge path not found: {root}")
        files = [root] if root.is_file() else sorted(
            p for p in root.rglob("*") if p.suffix.lower() in {".md", ".txt"}
        )
        for file in files:
            chunks.append(f"## Source: {file.name}\n\n{file.read_text(encoding='utf-8').strip()}")
    text = "\n\n".join(chunks)
    if len(text) > cfg.max_chars:
        raise ConfigError(
            f"knowledge base is {len(text)} characters, over the {cfg.max_chars} limit; "
            "trim it or raise knowledge.max_chars"
        )
    return text

"""Maps the `provider:` names used in config files to adapter classes.

Adapters are imported lazily so that, for example, a Gmail + Shopify deployment
never needs the Anthropic SDK installed. To add a platform, write a class that
implements the relevant base interface and register its import path here (or
call `register()` from your own code).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .config import Config, ConfigError

_REGISTRY: dict[str, dict[str, str]] = {
    "email": {
        "mock": "draftdesk.email.mock:MockEmailProvider",
        "graph": "draftdesk.email.graph:GraphEmailProvider",
        "microsoft365": "draftdesk.email.graph:GraphEmailProvider",
        "gmail": "draftdesk.email.gmail:GmailEmailProvider",
        "imap": "draftdesk.email.imap:ImapEmailProvider",
    },
    "commerce": {
        "none": "draftdesk.commerce.base:NoCommerceProvider",
        "mock": "draftdesk.commerce.mock:MockCommerceProvider",
        "bigcommerce": "draftdesk.commerce.bigcommerce:BigCommerceProvider",
        "shopify": "draftdesk.commerce.shopify:ShopifyProvider",
        "woocommerce": "draftdesk.commerce.woocommerce:WooCommerceProvider",
    },
    "llm": {
        "mock": "draftdesk.llm.mock:MockLLMProvider",
        "anthropic": "draftdesk.llm.anthropic:AnthropicProvider",
        "openai_compatible": "draftdesk.llm.openai_compatible:OpenAICompatibleProvider",
    },
}


def register(kind: str, name: str, import_path: str) -> None:
    """Register a custom adapter, e.g. register("commerce", "magento", "my_pkg.magento:Magento")."""
    _REGISTRY[kind][name] = import_path


def available(kind: str) -> list[str]:
    return sorted(_REGISTRY[kind])


def create(kind: str, name: str, options: dict[str, Any]) -> Any:
    try:
        module_name, _, class_name = _REGISTRY[kind][name].partition(":")
    except KeyError:
        raise ConfigError(
            f"unknown {kind} provider '{name}'; available: {', '.join(available(kind))}"
        ) from None
    cls = getattr(import_module(module_name), class_name)
    try:
        return cls(**options)
    except TypeError as exc:
        raise ConfigError(f"invalid options for {kind} provider '{name}': {exc}") from exc


def build_providers(config: Config) -> tuple[Any, Any, Any]:
    return (
        create("email", config.email.provider, config.email.options),
        create("commerce", config.commerce.provider, config.commerce.options),
        create("llm", config.llm.provider, config.llm.options),
    )

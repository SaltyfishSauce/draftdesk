"""The contract every model adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DraftRequest, DraftResult


class LLMError(RuntimeError):
    """The model could not produce a usable draft (refusal, truncation, bad output)."""


class LLMProvider(ABC):
    name: str = "llm"

    @abstractmethod
    def draft(self, request: DraftRequest) -> DraftResult:
        """Write a reply for one email.

        Adapters normally call `prompt.build_prompt(request)` and
        `prompt.parse_result(text)` so that every model sees the same
        instructions and returns the same structure. Raise LLMError when no
        usable draft comes back; the pipeline records it and moves on.
        """

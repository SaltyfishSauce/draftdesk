"""Sample inbox, orders and policies used by `draftdesk demo` and the tests."""

from pathlib import Path


def path(name: str) -> Path:
    return Path(__file__).parent / name

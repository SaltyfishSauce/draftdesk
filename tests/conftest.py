import pytest

from draftdesk.cli import build_pipeline, demo_config
from draftdesk.store import Store


@pytest.fixture
def pipeline():
    """The demo pipeline (mock mailbox, mock store, mock model) on an in-memory database."""
    return build_pipeline(demo_config(":memory:"), store=Store(":memory:"))


def by_message(pipeline, message_id):
    for status in ("pending", "in_mailbox", "sent", "rejected", "skipped", "error"):
        for draft in pipeline.store.list(status):
            if draft["message_id"] == message_id:
                return draft
    raise AssertionError(f"no draft for {message_id}")

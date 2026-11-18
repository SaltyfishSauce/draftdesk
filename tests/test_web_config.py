import re

import pytest
from fastapi.testclient import TestClient

from draftdesk.config import ConfigError, KnowledgeConfig, load_knowledge, parse_config
from draftdesk.registry import create
from draftdesk.web import create_app

from .conftest import by_message


def csrf_of(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_dashboard_review_flow(pipeline):
    pipeline.run_once()
    web = TestClient(create_app(pipeline))
    home = web.get("/")
    assert home.status_code == 200 and "Where is my order?" in home.text
    token = csrf_of(home.text)
    clean = by_message(pipeline, "demo-001")
    flagged = by_message(pipeline, "demo-004")
    # One-click send is offered for clean drafts only.
    assert f'action="/drafts/{clean["id"]}/send"' in home.text
    assert f'action="/drafts/{flagged["id"]}/send"' not in home.text

    assert web.post(f"/drafts/{clean['id']}/send", data={"csrf": "wrong"}).status_code == 403
    assert pipeline.email.sent == []

    sent = web.post(f"/drafts/{clean['id']}/send", data={"csrf": token}, follow_redirects=False)
    assert sent.status_code == 303 and len(pipeline.email.sent) == 1

    detail = web.get(f"/drafts/{flagged['id']}")
    assert "Sensitive topic" in detail.text and "<textarea" in detail.text
    web.post(f"/drafts/{flagged['id']}/send", data={"csrf": token, "body": "Edited by agent"})
    assert pipeline.email.sent[-1][1] == "Edited by agent"
    assert web.get("/healthz").json()["queue"]["sent"] == 2


def test_customer_text_is_escaped(pipeline):
    pipeline.email._emails[0].body_text = "<script>alert(1)</script>"
    pipeline.run_once()
    assert "<script>alert(1)</script>" not in TestClient(create_app(pipeline)).get("/").text


def test_dashboard_password(pipeline):
    web = TestClient(create_app(pipeline, password="s3cret"))
    assert web.get("/").status_code == 401
    assert web.get("/", auth=("dana", "nope")).status_code == 401
    assert web.get("/", auth=("dana", "s3cret")).status_code == 200


def test_config_env_interpolation(monkeypatch):
    raw = {"email": {"provider": "mock"}, "llm": {"provider": "openai_compatible", "model": "m", "api_key": "${MY_KEY}"}}
    with pytest.raises(ConfigError, match="MY_KEY"):
        parse_config(raw)
    monkeypatch.setenv("MY_KEY", "abc")
    cfg = parse_config(raw)
    assert cfg.llm.options == {"model": "m", "api_key": "abc"}
    assert cfg.commerce.provider == "none"
    assert parse_config({**raw, "storage_path": "${NOPE:-data/x.db}"}).storage_path == "data/x.db"


def test_config_errors_are_explicit(tmp_path):
    with pytest.raises(ConfigError, match="provider"):
        parse_config({"email": {}, "llm": {"provider": "mock"}})
    with pytest.raises(ConfigError, match="unknown commerce provider"):
        create("commerce", "magento", {})
    with pytest.raises(ConfigError, match="invalid options"):
        create("commerce", "bigcommerce", {"wrong": 1})
    (tmp_path / "kb.md").write_text("x" * 50)
    with pytest.raises(ConfigError, match="over the 10 limit"):
        load_knowledge(KnowledgeConfig(paths=[str(tmp_path)], max_chars=10))
    assert "Source: kb.md" in load_knowledge(KnowledgeConfig(paths=[str(tmp_path)]))

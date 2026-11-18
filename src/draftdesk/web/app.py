"""Review dashboard: the human-in-the-loop step.

Server-rendered on purpose. A support agent needs a queue, an editable draft
and a Send button; that does not justify a JavaScript build.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from ..pipeline import Pipeline, ReviewError
from ..store import STATUSES

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

FLAG_LABELS = {
    "model_requested_review": ("Model asked for review", "The model was unsure or the request needs a decision."),
    "sensitive_topic": ("Sensitive topic", "The email mentions refunds, disputes, legal or safety issues."),
    "possible_prompt_injection": ("Possible manipulation", "The email contains text that reads like instructions to the AI."),
    "order_belongs_to_other_customer": ("Order not theirs", "The order number mentioned belongs to a different email address. Its details were withheld from the model."),
    "no_order_found": ("No order found", "No order matched the sender's address."),
    "unverified_link": ("Unverified link", "The draft contains a link that is not in the knowledge base or order data."),
    "unverified_order_reference": ("Unverified order number", "The draft quotes an order number that was not in the looked-up data."),
    "unverified_tracking_number": ("Unverified tracking number", "The draft contains a tracking-like code that is not in the order data."),
}
TAB_LABELS = {
    "pending": "To review",
    "in_mailbox": "In mailbox drafts",
    "sent": "Sent",
    "rejected": "Rejected",
    "skipped": "Skipped",
    "error": "Errors",
}


def create_app(pipeline: Pipeline, password: str | None = None, title: str = "DraftDesk") -> FastAPI:
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)
    csrf_token = secrets.token_urlsafe(32)
    basic = HTTPBasic(auto_error=False)

    def auth(credentials: HTTPBasicCredentials | None = Depends(basic)) -> str:
        if not password:
            return "agent"
        if credentials is None or not secrets.compare_digest(
            credentials.password.encode(), password.encode()
        ):
            raise HTTPException(401, "Authentication required", {"WWW-Authenticate": "Basic"})
        return credentials.username or "agent"

    def check_csrf(token: str) -> None:
        if not secrets.compare_digest(token, csrf_token):
            raise HTTPException(403, "Invalid form token; reload the page and try again")

    def render(request: Request, name: str, **context) -> HTMLResponse:
        context.update(
            title=title,
            store_name=pipeline.config.store.name,
            counts=pipeline.store.counts(),
            tabs=TAB_LABELS,
            flag_labels=FLAG_LABELS,
            csrf=csrf_token,
            msg=request.query_params.get("msg", ""),
            err=request.query_params.get("err", ""),
            providers={
                "email": pipeline.email.name,
                "commerce": pipeline.commerce.name,
                "llm": pipeline.llm.name,
            },
        )
        return TEMPLATES.TemplateResponse(request, name, context)

    def back(url: str, msg: str = "", err: str = "") -> RedirectResponse:
        query = f"?msg={quote(msg)}" if msg else f"?err={quote(err)}" if err else ""
        return RedirectResponse(f"{url}{query}", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "queue": pipeline.store.counts()}

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, status: str = "pending", actor: str = Depends(auth)):
        if status not in STATUSES:
            status = "pending"
        return render(request, "queue.html", status=status, drafts=pipeline.store.list(status))

    @app.get("/drafts/{draft_id}", response_class=HTMLResponse)
    def detail(request: Request, draft_id: int, actor: str = Depends(auth)):
        draft = pipeline.store.get(draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        return render(request, "detail.html", draft=draft, audit=pipeline.store.audit_for(draft_id))

    @app.post("/drafts/{draft_id}/send")
    def send(
        draft_id: int,
        csrf: str = Form(...),
        body: str | None = Form(None),
        actor: str = Depends(auth),
    ):
        check_csrf(csrf)
        try:
            pipeline.approve_and_send(draft_id, body, actor=actor)
        except ReviewError as exc:
            return back(f"/drafts/{draft_id}", err=str(exc))
        return back("/", msg=f"Reply #{draft_id} sent")

    @app.post("/drafts/{draft_id}/reject")
    def reject(
        draft_id: int, csrf: str = Form(...), reason: str = Form(""), actor: str = Depends(auth)
    ):
        check_csrf(csrf)
        try:
            pipeline.reject(draft_id, reason, actor=actor)
        except ReviewError as exc:
            return back(f"/drafts/{draft_id}", err=str(exc))
        return back("/", msg=f"Draft #{draft_id} rejected; reply to this customer manually")

    @app.post("/drafts/{draft_id}/regenerate")
    def regenerate(
        draft_id: int, csrf: str = Form(...), instruction: str = Form(""), actor: str = Depends(auth)
    ):
        check_csrf(csrf)
        try:
            pipeline.regenerate(draft_id, instruction, actor=actor)
        except ReviewError as exc:
            return back(f"/drafts/{draft_id}", err=str(exc))
        return back(f"/drafts/{draft_id}", msg="Draft regenerated")

    @app.post("/poll")
    def poll(csrf: str = Form(...), actor: str = Depends(auth)):
        check_csrf(csrf)
        try:
            summary = pipeline.run_once()
        except Exception as exc:  # noqa: BLE001 - surface provider failures to the agent
            return back("/", err=f"Inbox check failed: {exc}")
        return back(
            "/",
            msg=f"Inbox checked: {summary.drafted} drafted, {summary.skipped} skipped, {summary.errors} errors",
        )

    return app

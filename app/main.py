"""FastAPI backend for CompliFlow Lite. Serves the API and the single-page UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .orchestrator import (
    continue_after_approvals,
    get_snapshot,
    run_workflow,
)
from .rag import get_store, ingest, reload_store

app = FastAPI(title="CompliFlow Lite", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


class ChatRequest(BaseModel):
    message: str
    requester: str = "employee@acmecorp.example"
    injected_document: str | None = None


class ApprovalDecision(BaseModel):
    approval_id: str
    status: str  # approved | rejected
    reason: str | None = None


@app.get("/api/health")
def health() -> dict:
    store = get_store()
    return {
        "status": "ok",
        "api_key_present": config.has_api_key(),
        "rag_ready": store.ready,
        "policy_chunks": len(store.chunks),
        "fast_model": config.FAST_MODEL,
        "reasoning_model": config.REASONING_MODEL,
        "embedding_model": config.EMBEDDING_MODEL,
    }


@app.post("/api/ingest")
def api_ingest() -> dict:
    n = ingest()
    reload_store()
    return {"ingested_chunks": n}


@app.post("/api/chat")
def api_chat(req: ChatRequest) -> dict:
    if not req.message.strip():
        raise HTTPException(400, "empty message")
    return run_workflow(req.message, req.requester, req.injected_document)


@app.get("/api/workflows")
def api_workflows() -> list[dict]:
    return db.list_workflows()


@app.get("/api/workflows/{wid}")
def api_workflow(wid: str) -> dict:
    wf = db.get_workflow(wid)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return get_snapshot(wid)


@app.get("/api/approvals/pending")
def api_pending() -> list[dict]:
    return db.list_pending_approvals()


@app.post("/api/approvals/decide")
def api_decide(d: ApprovalDecision) -> dict:
    if d.status not in ("approved", "rejected"):
        raise HTTPException(400, "status must be approved|rejected")
    # find workflow for this approval
    with db.connect() as conn:
        row = conn.execute(
            "SELECT workflow_id FROM approvals WHERE id=?", (d.approval_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "approval not found")
    wid = row["workflow_id"]
    db.decide_approval(d.approval_id, d.status, d.reason)
    db.add_audit(wid, "approval_decision",
                 f"Approval {d.approval_id} -> {d.status}", {"reason": d.reason})
    return continue_after_approvals(wid)


@app.get("/api/audit/{wid}")
def api_audit(wid: str) -> list[dict]:
    return db.list_audit(wid)


# -------- static UI --------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"detail": "UI not built"}, status_code=404)

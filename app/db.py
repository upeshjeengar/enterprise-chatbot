"""SQLite persistence layer. Mirrors the spec's schema:
workflows, workflow_steps, approvals, tool_calls, audit_events,
policy_citations, generated_documents."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    requester TEXT,
    department TEXT,
    vendor_name TEXT,
    contract_value_inr INTEGER,
    status TEXT NOT NULL,
    risk_level TEXT,
    summary TEXT,
    payload_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    approver_role TEXT NOT NULL,
    approver_name TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    tool_name TEXT NOT NULL,
    input_json TEXT,
    output_json TEXT,
    risk_level TEXT,
    approval_required INTEGER,
    status TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS policy_citations (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    policy_name TEXT,
    section TEXT,
    chunk_text TEXT,
    relevance_score REAL
);
CREATE TABLE IF NOT EXISTS generated_documents (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    doc_type TEXT,
    title TEXT,
    content TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:12]) if prefix else uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------- workflows ----------------
def create_workflow(wf: dict) -> str:
    wid = wf.get("id") or ("WF-" + new_id()[:8])
    with connect() as conn:
        conn.execute(
            """INSERT INTO workflows
            (id, workflow_type, requester, department, vendor_name,
             contract_value_inr, status, risk_level, summary, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                wid,
                wf.get("workflow_type", "unknown"),
                wf.get("requester", "employee@acmecorp.example"),
                wf.get("department"),
                wf.get("vendor_name"),
                wf.get("contract_value_inr"),
                wf.get("status", "DRAFT"),
                wf.get("risk_level"),
                wf.get("summary"),
                _dumps(wf.get("payload", {})),
            ),
        )
    return wid


def update_workflow(wid: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values())
    with connect() as conn:
        conn.execute(
            f"UPDATE workflows SET {sets}, updated_at=? WHERE id=?",
            (*vals, _now(), wid),
        )


def get_workflow(wid: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
    return dict(row) if row else None


def list_workflows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workflows ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- approvals ----------------
def add_approval(workflow_id: str, role: str, name: str | None = None) -> str:
    aid = new_id("AP-")
    with connect() as conn:
        conn.execute(
            """INSERT INTO approvals (id, workflow_id, approver_role, approver_name, status)
               VALUES (?,?,?,?,?)""",
            (aid, workflow_id, role, name, "pending"),
        )
    return aid


def decide_approval(approval_id: str, status: str, reason: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE approvals SET status=?, reason=?, decided_at=? WHERE id=?",
            (status, reason, _now(), approval_id),
        )


def list_approvals(workflow_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE workflow_id=? ORDER BY created_at", (workflow_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_pending_approvals() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.*, w.vendor_name, w.workflow_type, w.contract_value_inr, w.risk_level
               FROM approvals a JOIN workflows w ON a.workflow_id = w.id
               WHERE a.status='pending' ORDER BY a.created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- tool calls ----------------
def add_tool_call(
    workflow_id: str,
    tool_name: str,
    input_obj: Any,
    output_obj: Any,
    risk_level: str,
    approval_required: bool,
    status: str,
) -> str:
    tid = new_id("TC-")
    with connect() as conn:
        conn.execute(
            """INSERT INTO tool_calls
               (id, workflow_id, tool_name, input_json, output_json,
                risk_level, approval_required, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                tid,
                workflow_id,
                tool_name,
                _dumps(input_obj),
                _dumps(output_obj),
                risk_level,
                int(approval_required),
                status,
            ),
        )
    return tid


def list_tool_calls(workflow_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tool_calls WHERE workflow_id=? ORDER BY created_at", (workflow_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- audit events ----------------
def add_audit(
    workflow_id: str, event_type: str, message: str, metadata: Any = None
) -> str:
    eid = new_id("EV-")
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_events (id, workflow_id, event_type, message, metadata)
               VALUES (?,?,?,?,?)""",
            (eid, workflow_id, event_type, message, _dumps(metadata or {})),
        )
    return eid


def list_audit(workflow_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE workflow_id=? ORDER BY created_at", (workflow_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- policy citations ----------------
def add_citations(workflow_id: str, citations: list[dict]) -> None:
    with connect() as conn:
        for c in citations:
            conn.execute(
                """INSERT INTO policy_citations
                   (id, workflow_id, policy_name, section, chunk_text, relevance_score)
                   VALUES (?,?,?,?,?,?)""",
                (
                    new_id("PC-"),
                    workflow_id,
                    c.get("policy_name"),
                    c.get("section"),
                    c.get("text", "")[:1200],
                    c.get("relevance_score"),
                ),
            )


def list_citations(workflow_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM policy_citations WHERE workflow_id=? ORDER BY relevance_score DESC",
            (workflow_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- generated documents ----------------
def add_document(workflow_id: str, doc_type: str, title: str, content: str) -> str:
    did = new_id("DOC-")
    with connect() as conn:
        conn.execute(
            """INSERT INTO generated_documents (id, workflow_id, doc_type, title, content)
               VALUES (?,?,?,?,?)""",
            (did, workflow_id, doc_type, title, content),
        )
    return did


def list_documents(workflow_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_documents WHERE workflow_id=? ORDER BY created_at",
            (workflow_id,),
        ).fetchall()
    return [dict(r) for r in rows]

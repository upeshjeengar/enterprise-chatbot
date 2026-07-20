"""Mock enterprise systems (Jira/ServiceNow, SAP Ariba procurement, DocuSign legal,
Okta IAM, Slack/email notifications). All in-process, deterministic, and logged.
Each returns a ticket-like object as a real SaaS API would. No network is touched;
the dummy credentials in data/credentials are 'used' only for realism/logging."""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db

_CREDS: dict = {}

# In-memory record store so stateful mock endpoints (update status, add
# comment, poll status, download) stay coherent within a process. Keyed by
# bucket -> record_id -> record. Deterministic; reset on restart.
_STORE: dict[str, dict[str, dict]] = {
    "tickets": {}, "requisitions": {}, "envelopes": {},
    "users": {}, "channels": {},
}


def _bucket(name: str) -> dict:
    return _STORE.setdefault(name, {})


def _creds() -> dict:
    global _CREDS
    if not _CREDS:
        p = Path(config.CREDENTIAL_DIR) / "enterprise_credentials.json"
        _CREDS = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _CREDS


def _integration(name: str) -> dict:
    return _creds().get("integrations", {}).get(name, {})


# --- procurement (SAP Ariba mock) -----------------------------------------
def create_purchase_request(workflow_id: str, vendor: str, amount_inr: int,
                            department: str, justification: str) -> dict:
    ticket = "PR-" + db.new_id()[:6].upper()
    return {
        "system": "SAP Ariba (mock)",
        "realm": _integration("sap_ariba_procurement").get("realm", "acmecorp"),
        "ticket_id": ticket,
        "vendor": vendor,
        "amount_inr": amount_inr,
        "department": department,
        "business_justification": justification,
        "status": "created",
    }


# --- legal (DocuSign / NDA mock) ------------------------------------------
def create_legal_review(workflow_id: str, vendor: str, doc_type: str = "NDA") -> dict:
    ticket = "LEG-" + db.new_id()[:6].upper()
    return {
        "system": "DocuSign eSignature (mock)",
        "account_id": _integration("docusign_legal").get("account_id", "mock"),
        "ticket_id": ticket,
        "vendor": vendor,
        "document": doc_type,
        "template": "LEG-TMPL-NDA-STD",
        "status": "draft_routed_to_legal",
        "signed": False,
    }


# --- security review (mock) -----------------------------------------------
def create_security_review(workflow_id: str, vendor: str, scope: list[str]) -> dict:
    ticket = "SEC-" + db.new_id()[:6].upper()
    return {
        "system": "Security Review Queue (mock)",
        "ticket_id": ticket,
        "vendor": vendor,
        "requested_scope": scope,
        "status": "pending_review",
    }


# --- IAM / access provisioning (Okta mock) --------------------------------
def create_access_request(workflow_id: str, vendor: str, access: list[str],
                          duration_days: int | None) -> dict:
    ticket = "IAM-" + db.new_id()[:6].upper()
    return {
        "system": "Okta Workforce Identity (mock)",
        "org_url": _integration("okta_iam").get("org_url", "https://acmecorp.okta.com"),
        "ticket_id": ticket,
        "vendor": vendor,
        "requested_access": access,
        "duration_days": duration_days or 90,
        "status": "requested_awaiting_approval",
        "provisioned": False,
    }


# --- notifications (Slack / email mock) -----------------------------------
def send_notification(workflow_id: str, to_role: str, message: str) -> dict:
    return {
        "system": "Slack Enterprise Grid (mock)",
        "workspace": _integration("slack_enterprise").get("workspace", "acmecorp"),
        "to": to_role,
        "message": message,
        "status": "sent",
    }


# --- project tracking (Jira mock) -----------------------------------------
def create_project_task(workflow_id: str, project: str, summary: str,
                        issue_type: str = "Task") -> dict:
    key = (project or "TASK").upper().split()[0]
    ticket = f"{key}-" + db.new_id()[:4].upper()
    return {
        "system": "Jira Software (mock)",
        "site": _integration("jira").get("base_url", "https://acmecorp.atlassian.net"),
        "ticket_id": ticket,
        "project": key,
        "issue_type": issue_type,
        "summary": summary,
        "status": "created",
    }


# --- IT service management (ServiceNow mock) ------------------------------
def create_itsm_ticket(workflow_id: str, short_description: str,
                       category: str = "hardware") -> dict:
    ticket = "INC-" + db.new_id()[:6].upper()
    return {
        "system": "ServiceNow ITSM (mock)",
        "instance": _integration("servicenow").get("instance", "https://acmecorp.service-now.com"),
        "ticket_id": ticket,
        "short_description": short_description,
        "category": category,
        "status": "new",
    }


# ==========================================================================
# Extended REST-style mock endpoints. Each mirrors the shape of a real SaaS
# API (path + payload documented in SUPPORTED_ENDPOINTS below) but runs fully
# in-process against _STORE. Return values are plain dicts, as an HTTP JSON
# body would be. Validation is best-effort and returns an {"error": ...} dict
# rather than raising, so callers/agents degrade gracefully.
# ==========================================================================

_VALID_TICKET_STATUS = {"Open", "In Progress", "Resolved", "Closed"}
_VALID_PR_DECISION = {"Approve", "Reject"}


# --- 1. Jira / ServiceNow (Ticketing & ITSM) ------------------------------
def ticket_create(title: str, description: str = "", priority: str = "Medium",
                  reporter_email: str = "") -> dict:
    """POST /api/v1/tickets -> open a new incident. Returns ticket_id, status=Open."""
    tid = "TCK-" + db.new_id()[:6].upper()
    rec = {
        "system": "Jira/ServiceNow Ticketing (mock)",
        "ticket_id": tid,
        "title": title,
        "description": description,
        "priority": priority,
        "reporter_email": reporter_email,
        "status": "Open",
        "comments": [],
    }
    _bucket("tickets")[tid] = rec
    return dict(rec)


def ticket_update_status(ticket_id: str, status: str) -> dict:
    """PATCH /api/v1/tickets/{ticket_id} -> move a ticket through its workflow."""
    rec = _bucket("tickets").get(ticket_id)
    if not rec:
        return {"error": "ticket_not_found", "ticket_id": ticket_id}
    if status not in _VALID_TICKET_STATUS:
        return {"error": "invalid_status", "allowed": sorted(_VALID_TICKET_STATUS)}
    rec["status"] = status
    return {"ticket_id": ticket_id, "status": status, "updated": True}


def ticket_add_comment(ticket_id: str, author_id: str, comment_text: str) -> dict:
    """POST /api/v1/tickets/{ticket_id}/comments -> add a note to a ticket."""
    rec = _bucket("tickets").get(ticket_id)
    if not rec:
        return {"error": "ticket_not_found", "ticket_id": ticket_id}
    comment = {
        "comment_id": "CMT-" + db.new_id()[:4].upper(),
        "author_id": author_id,
        "comment_text": comment_text,
    }
    rec["comments"].append(comment)
    return {"ticket_id": ticket_id, **comment, "comment_count": len(rec["comments"])}


# --- 2. SAP Ariba (Procurement) -------------------------------------------
def requisition_create(requester_id: str, items: list[dict],
                       department_code: str) -> dict:
    """POST /api/v1/requisitions -> employee requests a purchase.
    items: [{"name": str, "cost": number, "qty"?: int}]. Returns pr_id, status=Pending Approval."""
    items = items or []
    total = 0.0
    for it in items:
        cost = it.get("cost", 0) or 0
        qty = it.get("qty", 1) or 1
        total += cost * qty
    pr_id = "PR-" + db.new_id()[:6].upper()
    rec = {
        "system": "SAP Ariba (mock)",
        "realm": _integration("sap_ariba_procurement").get("realm", "acmecorp"),
        "pr_id": pr_id,
        "requester_id": requester_id,
        "items": items,
        "department_code": department_code,
        "total_cost": total,
        "status": "Pending Approval",
        "approvals": [],
    }
    _bucket("requisitions")[pr_id] = rec
    return {k: v for k, v in rec.items() if k != "approvals"}


def requisition_decision(pr_id: str, approver_id: str, decision: str,
                         comments: str = "") -> dict:
    """POST /api/v1/requisitions/{pr_id}/approval -> manager approves/rejects the spend."""
    rec = _bucket("requisitions").get(pr_id)
    if not rec:
        return {"error": "requisition_not_found", "pr_id": pr_id}
    if decision not in _VALID_PR_DECISION:
        return {"error": "invalid_decision", "allowed": sorted(_VALID_PR_DECISION)}
    rec["approvals"].append({"approver_id": approver_id, "decision": decision,
                             "comments": comments})
    rec["status"] = "Approved" if decision == "Approve" else "Rejected"
    return {"pr_id": pr_id, "decision": decision, "status": rec["status"]}


def requisition_status(pr_id: str) -> dict:
    """GET /api/v1/requisitions/{pr_id}/status -> poll where the PR sits in the workflow."""
    rec = _bucket("requisitions").get(pr_id)
    if not rec:
        return {"error": "requisition_not_found", "pr_id": pr_id}
    return {"pr_id": pr_id, "status": rec["status"], "total_cost": rec["total_cost"]}


# --- 3. DocuSign (Legal & e-Signature) ------------------------------------
def envelope_create(email_subject: str, signers: list[dict],
                    document_base64: str = "MOCK_DOC") -> dict:
    """POST /api/v1/envelopes -> send a contract out for signature.
    signers: [{"name": str, "email": str}]. Returns envelope_id."""
    env_id = "ENV-" + db.new_id()[:6].upper()
    doc_id = "DOC-" + db.new_id()[:4].upper()
    rec = {
        "system": "DocuSign eSignature (mock)",
        "account_id": _integration("docusign_legal").get("account_id", "mock"),
        "envelope_id": env_id,
        "document_id": doc_id,
        "email_subject": email_subject,
        "signers": signers or [],
        "has_document": bool(document_base64),
        "status": "Sent",
    }
    _bucket("envelopes")[env_id] = rec
    return {k: v for k, v in rec.items() if k != "has_document"}


def envelope_status(envelope_id: str) -> dict:
    """GET /api/v1/envelopes/{envelope_id}/status -> check if recipients have signed."""
    rec = _bucket("envelopes").get(envelope_id)
    if not rec:
        return {"error": "envelope_not_found", "envelope_id": envelope_id}
    return {"envelope_id": envelope_id, "status": rec["status"]}


def envelope_download(envelope_id: str, document_id: str) -> dict:
    """GET /api/v1/envelopes/{envelope_id}/documents/{document_id} -> retrieve executed PDF.
    Only succeeds once the envelope status is 'Completed'."""
    rec = _bucket("envelopes").get(envelope_id)
    if not rec:
        return {"error": "envelope_not_found", "envelope_id": envelope_id}
    if rec["status"] != "Completed":
        return {"error": "not_completed", "status": rec["status"],
                "hint": "document available only after signing completes"}
    if document_id != rec["document_id"]:
        return {"error": "document_not_found", "document_id": document_id}
    return {
        "envelope_id": envelope_id,
        "document_id": document_id,
        "filename": f"{envelope_id}-executed.pdf",
        "content_base64": "MOCK_SIGNED_PDF_BYTES",
    }


# --- 4. Okta (Identity & Access Management) -------------------------------
def user_provision(first_name: str, last_name: str, email: str,
                   department: str = "") -> dict:
    """POST /api/v1/users -> onboard a new employee into the directory.
    Returns okta_user_id, status=Staged."""
    uid = "OKT-" + db.new_id()[:6].upper()
    rec = {
        "system": "Okta Workforce Identity (mock)",
        "org_url": _integration("okta_iam").get("org_url", "https://acmecorp.okta.com"),
        "okta_user_id": uid,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "department": department,
        "status": "Staged",
        "assigned_apps": [],
    }
    _bucket("users")[uid] = rec
    return {k: v for k, v in rec.items() if k != "assigned_apps"}


def user_assign_app(user_id: str, app_id: str) -> dict:
    """POST /api/v1/users/{user_id}/apps/{app_id} -> grant an app license to a user."""
    rec = _bucket("users").get(user_id)
    if not rec:
        return {"error": "user_not_found", "user_id": user_id}
    if app_id not in rec["assigned_apps"]:
        rec["assigned_apps"].append(app_id)
    if rec["status"] == "Staged":
        rec["status"] = "Active"
    return {"user_id": user_id, "app_id": app_id, "assigned": True,
            "assigned_apps": list(rec["assigned_apps"])}


def user_deactivate(user_id: str) -> dict:
    """POST /api/v1/users/{user_id}/lifecycle/deactivate -> offboard, revoke access."""
    rec = _bucket("users").get(user_id)
    if not rec:
        return {"error": "user_not_found", "user_id": user_id}
    rec["status"] = "Deactivated"
    rec["assigned_apps"] = []
    return {"user_id": user_id, "status": "Deactivated", "access_revoked": True}


# --- 5. Slack (Communication & Collaboration) -----------------------------
def chat_post_message(channel_id: str, text: str) -> dict:
    """POST /api/v1/chat.postMessage -> bot/integration sends an alert."""
    return {
        "system": "Slack Enterprise Grid (mock)",
        "workspace": _integration("slack_enterprise").get("workspace", "acmecorp"),
        "channel_id": channel_id,
        "text": text,
        "ts": db.new_id()[:10],
        "ok": True,
    }


def conversations_create(channel_name: str, is_private: bool = False) -> dict:
    """POST /api/v1/conversations.create -> spin up a dedicated channel."""
    cid = "C" + db.new_id()[:8].upper()
    rec = {
        "system": "Slack Enterprise Grid (mock)",
        "channel_id": cid,
        "channel_name": channel_name,
        "is_private": bool(is_private),
        "members": [],
        "ok": True,
    }
    _bucket("channels")[cid] = rec
    return {k: v for k, v in rec.items() if k != "members"}


def conversations_invite(channel_id: str, user_ids: list[str]) -> dict:
    """POST /api/v1/conversations.invite -> pull employees into a channel."""
    rec = _bucket("channels").get(channel_id)
    if not rec:
        return {"error": "channel_not_found", "channel_id": channel_id}
    for u in user_ids or []:
        if u not in rec["members"]:
            rec["members"].append(u)
    return {"channel_id": channel_id, "invited": list(user_ids or []),
            "members": list(rec["members"]), "ok": True}


# --- documentation: supported REST-style mock endpoints --------------------
# Grouped by system. Each entry: (method, path, callable, required-payload).
# Purely descriptive — surfaced by /health and the demo UI so reviewers can
# see the simulated surface area without reading the code.
SUPPORTED_ENDPOINTS: dict[str, list[dict]] = {
    "Jira / ServiceNow (Ticketing & ITSM)": [
        {"method": "POST", "path": "/api/v1/tickets", "fn": "ticket_create",
         "payload": ["title", "description", "priority", "reporter_email"],
         "action": "Open a new incident; returns ticket_id, status=Open."},
        {"method": "PATCH", "path": "/api/v1/tickets/{ticket_id}", "fn": "ticket_update_status",
         "payload": ["status (In Progress|Resolved|Closed)"],
         "action": "Move a ticket through its workflow."},
        {"method": "POST", "path": "/api/v1/tickets/{ticket_id}/comments", "fn": "ticket_add_comment",
         "payload": ["author_id", "comment_text"],
         "action": "Add a note to a ticket."},
    ],
    "SAP Ariba (Procurement)": [
        {"method": "POST", "path": "/api/v1/requisitions", "fn": "requisition_create",
         "payload": ["requester_id", "items[]", "department_code"],
         "action": "Create a purchase requisition; returns pr_id, status=Pending Approval."},
        {"method": "POST", "path": "/api/v1/requisitions/{pr_id}/approval", "fn": "requisition_decision",
         "payload": ["approver_id", "decision (Approve|Reject)", "comments"],
         "action": "Approve or reject the spend."},
        {"method": "GET", "path": "/api/v1/requisitions/{pr_id}/status", "fn": "requisition_status",
         "payload": [], "action": "Poll where the PR sits in the workflow."},
    ],
    "DocuSign (Legal & e-Signature)": [
        {"method": "POST", "path": "/api/v1/envelopes", "fn": "envelope_create",
         "payload": ["document_base64", "email_subject", "signers[]"],
         "action": "Send a contract for signature; returns envelope_id."},
        {"method": "GET", "path": "/api/v1/envelopes/{envelope_id}/status", "fn": "envelope_status",
         "payload": [], "action": "Check Sent/Delivered/Completed/Declined."},
        {"method": "GET", "path": "/api/v1/envelopes/{envelope_id}/documents/{document_id}",
         "fn": "envelope_download", "payload": [],
         "action": "Download executed PDF once Completed."},
    ],
    "Okta (Identity & Access Management)": [
        {"method": "POST", "path": "/api/v1/users", "fn": "user_provision",
         "payload": ["first_name", "last_name", "email", "department"],
         "action": "Provision a new user; returns okta_user_id, status=Staged."},
        {"method": "POST", "path": "/api/v1/users/{user_id}/apps/{app_id}", "fn": "user_assign_app",
         "payload": [], "action": "Assign an app license to a user."},
        {"method": "POST", "path": "/api/v1/users/{user_id}/lifecycle/deactivate", "fn": "user_deactivate",
         "payload": [], "action": "Deactivate user and revoke access."},
    ],
    "Slack (Communication & Collaboration)": [
        {"method": "POST", "path": "/api/v1/chat.postMessage", "fn": "chat_post_message",
         "payload": ["channel_id", "text"], "action": "Post an alert message."},
        {"method": "POST", "path": "/api/v1/conversations.create", "fn": "conversations_create",
         "payload": ["channel_name", "is_private"], "action": "Create a channel."},
        {"method": "POST", "path": "/api/v1/conversations.invite", "fn": "conversations_invite",
         "payload": ["channel_id", "user_ids[]"], "action": "Invite users to a channel."},
    ],
}


# --- registry: name -> (callable, risk, needs_human_approval) -------------
# High-risk tools are recorded but BLOCKED from executing their real effect.
TOOL_RISK = {
    "create_purchase_request": ("low", False),
    "create_legal_review": ("low", False),
    "create_security_review": ("low", False),
    "create_access_request": ("medium", False),   # creates request only; provisioning blocked
    "create_project_task": ("low", False),
    "create_itsm_ticket": ("low", False),
    "send_notification": ("low", False),
    # extended REST-style endpoints (stateful, in-process; safe simulations)
    "ticket_create": ("low", False),
    "ticket_update_status": ("low", False),
    "ticket_add_comment": ("low", False),
    "requisition_create": ("low", False),
    "requisition_decision": ("medium", False),   # records a decision only
    "requisition_status": ("low", False),
    "envelope_create": ("low", False),
    "envelope_status": ("low", False),
    "envelope_download": ("low", False),
    "user_provision": ("medium", False),          # staged only; not activated for prod
    "user_assign_app": ("medium", False),
    "user_deactivate": ("medium", False),
    "chat_post_message": ("low", False),
    "conversations_create": ("low", False),
    "conversations_invite": ("low", False),
    # hard-blocked effects
    "provision_access": ("high", True),
    "grant_production_access": ("high", True),
    "approve_payment": ("high", True),
    "release_payment": ("high", True),
    "send_contract_external": ("high", True),
    "mark_nda_signed": ("high", True),
}

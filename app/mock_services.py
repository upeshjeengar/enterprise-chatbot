"""Mock enterprise systems (Jira/ServiceNow, SAP Ariba procurement, DocuSign legal,
Okta IAM, Slack/email notifications). All in-process, deterministic, and logged.
Each returns a ticket-like object as a real SaaS API would. No network is touched;
the dummy credentials in data/credentials are 'used' only for realism/logging."""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db

_CREDS: dict = {}


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
    # hard-blocked effects
    "provision_access": ("high", True),
    "grant_production_access": ("high", True),
    "approve_payment": ("high", True),
    "release_payment": ("high", True),
    "send_contract_external": ("high", True),
    "mark_nda_signed": ("high", True),
}

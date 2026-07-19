"""Multi-agent orchestrator (supervisor pattern).

Agents (spec section 5):
  IntakeAgent        — NL -> structured workflow data (LLM, fast model)
  PlanningAgent      — decompose into ordered subtasks (LLM, fast model)
  PolicyRAGAgent     — retrieve relevant policy chunks (RAG)
  ComplianceRiskAgent— allowed/blocked/needs-approval + risk (LLM reasoning + deterministic rules)
  ApprovalRouterAgent— choose approvers (deterministic rules)
  DocumentAgent      — generate NDA/vendor/approval-summary docs (LLM)
  ToolExecutionAgent — call mock tools with a per-tool safety gate
  AuditAgent         — timeline + narrative (deterministic + LLM summary)
  GuardrailAgent     — input/retrieval/tool rails (see guardrails.py)

The orchestrator combines LLM reasoning with deterministic controls so safety
never depends solely on the model.
"""
from __future__ import annotations

import json
from typing import Any

from . import config, db, guardrails, mock_services
from .llm_gateway import chat, chat_json
from .rag import get_store


# ======================================================================== #
# Intake
# ======================================================================== #
def intake_agent(user_text: str) -> dict:
    prompt = f"""Extract a structured enterprise workflow request from the employee message.
Return JSON with exactly these keys:
  workflow_type: one of ["vendor_onboarding","it_access_request","procurement","travel_approval","software_license","other"]
  vendor_name: string or null
  department: string or null — the sponsoring team/department. Phrases like "for Marketing", "for the Design team", "for Analytics", "for IT", "for Ops" name the department (here: "Marketing", "Design", "Analytics", "IT", "Ops"). Acronyms (IT, HR, Ops, Eng) are valid departments.
  contract_value_inr: integer rupees or null (convert lakh: 1 lakh = 100000; crore: 1 crore = 10000000)
  requested_access: array of short access identifiers (e.g. ["analytics_dashboard","github"]) or []
  legal_documents: array (e.g. ["NDA"]) or []
  business_owner: string or null
  access_duration_days: integer or null
  missing_fields: array of the important fields that are genuinely absent from the message (do NOT list a field you were able to fill)
  intent_summary: one short sentence

Employee message:
\"\"\"{user_text}\"\"\"
"""
    try:
        data = chat_json([{"role": "user", "content": prompt}], reasoning=False)
    except Exception:
        data = {}
    # normalize
    data.setdefault("workflow_type", "vendor_onboarding")
    data.setdefault("vendor_name", None)
    data.setdefault("department", None)
    data.setdefault("contract_value_inr", None)
    data.setdefault("requested_access", [])
    data.setdefault("legal_documents", [])
    data.setdefault("business_owner", None)
    data.setdefault("access_duration_days", None)
    data.setdefault("missing_fields", [])
    data.setdefault("intent_summary", user_text[:120])
    # coerce types
    if isinstance(data["requested_access"], str):
        data["requested_access"] = [data["requested_access"]]
    if isinstance(data["legal_documents"], str):
        data["legal_documents"] = [data["legal_documents"]]
    try:
        if data["contract_value_inr"] is not None:
            data["contract_value_inr"] = int(data["contract_value_inr"])
    except (ValueError, TypeError):
        data["contract_value_inr"] = None
    return data


# ======================================================================== #
# Planning
# ======================================================================== #
def planning_agent(intake: dict) -> list[dict]:
    # Deterministic backbone plan; the sequence is policy-driven, not model-driven.
    plan = [
        {"step": 1, "task": "retrieve_relevant_policies", "agent": "policy_rag_agent"},
        {"step": 2, "task": "assess_compliance_and_risk", "agent": "compliance_agent"},
        {"step": 3, "task": "route_approvals", "agent": "approval_router_agent"},
        {"step": 4, "task": "generate_documents", "agent": "document_agent"},
        {"step": 5, "task": "execute_safe_tools", "agent": "tool_execution_agent"},
        {"step": 6, "task": "generate_audit_report", "agent": "audit_agent"},
    ]
    return plan


# ======================================================================== #
# Policy RAG
# ======================================================================== #
def policy_rag_agent(intake: dict, user_text: str) -> list[dict]:
    store = get_store()
    query_bits = [user_text, intake.get("intent_summary", "")]
    if intake.get("requested_access"):
        query_bits.append("data access " + " ".join(map(str, intake["requested_access"])))
    if intake.get("legal_documents"):
        query_bits.append("NDA legal " + " ".join(map(str, intake["legal_documents"])))
    if intake.get("contract_value_inr"):
        query_bits.append("procurement approval threshold contract value")
    query = " ".join(query_bits)
    return store.retrieve(query, k=6)


# ======================================================================== #
# Compliance & Risk (deterministic rules + LLM rationale)
# ======================================================================== #
def _risk_and_controls(intake: dict) -> dict:
    value = intake.get("contract_value_inr") or 0
    access = {str(a).lower() for a in intake.get("requested_access", [])}
    legal_docs = {str(d).upper() for d in intake.get("legal_documents", [])}

    high_risk_access = access & config.HIGH_RISK_ACCESS
    sensitive_access = access & config.SENSITIVE_ACCESS

    # risk level
    if value > config.THRESHOLD_FINANCE or sensitive_access or high_risk_access:
        risk = "high"
    elif value > config.THRESHOLD_MANAGER or access:
        risk = "medium"
    else:
        risk = "low"

    blocked_actions: list[str] = []
    if high_risk_access:
        blocked_actions.append("grant_production_access")
    if sensitive_access:
        blocked_actions.append("provision_access")
    # never auto: payments, external sharing
    blocked_actions += ["approve_payment", "send_contract_external"]

    auto_allowed = [
        "create_purchase_request",
        "create_legal_review",
        "create_security_review",
        "create_access_request",  # request only; provisioning gated
        "send_notification",
    ]

    reasons = []
    if value > config.THRESHOLD_CFO:
        reasons.append(f"Contract ₹{value:,} exceeds ₹10 lakh — CFO co-approval required.")
    elif value > config.THRESHOLD_FINANCE:
        reasons.append(f"Contract ₹{value:,} exceeds ₹5 lakh — Finance approval required.")
    elif value > config.THRESHOLD_MANAGER:
        reasons.append(f"Contract ₹{value:,} is ₹2–5 lakh — Department Head approval required.")
    if sensitive_access:
        reasons.append(f"Access {sorted(sensitive_access)} may expose customer/analytics data — Security approval required, provisioning blocked until approved.")
    if high_risk_access:
        reasons.append(f"Access {sorted(high_risk_access)} is production/admin class — an AI agent may never grant it; hard-blocked.")
    if legal_docs:
        reasons.append(f"Legal document(s) {sorted(legal_docs)} requested — Legal review required before execution.")
    if not reasons:
        reasons.append("Low-risk request within auto-allowed bounds.")

    return {
        "risk_level": risk,
        "blocked_actions": sorted(set(blocked_actions)),
        "auto_allowed_actions": auto_allowed,
        "reason": " ".join(reasons),
    }


def compliance_agent(intake: dict, citations: list[dict]) -> dict:
    controls = _risk_and_controls(intake)
    # LLM adds a grounded natural-language rationale citing retrieved policy,
    # but the machine-actionable decision comes from deterministic controls.
    cite_txt = "\n".join(
        f"- {c['policy_name']} [{c['section']}] (score {c['relevance_score']}): {c['text'][:220]}"
        for c in citations[:5]
    )
    prompt = f"""You are a compliance analyst. Using ONLY the retrieved policy excerpts,
write a 2-3 sentence rationale for the compliance decision. Do not invent thresholds.

Structured request: {json.dumps(intake, ensure_ascii=False)}
Deterministic decision: risk={controls['risk_level']}, blocked={controls['blocked_actions']}

Retrieved policies:
{cite_txt}

Return JSON: {{"rationale": "...", "policy_sections_cited": ["...", "..."]}}"""
    try:
        llm = chat_json(
            [
                {"role": "system", "content": "detailed thinking off"},
                {"role": "user", "content": prompt},
            ],
            reasoning=True, max_tokens=600, timeout=30.0, retries=2,
        )
        controls["rationale"] = llm.get("rationale", controls["reason"])
        controls["policy_sections_cited"] = llm.get("policy_sections_cited", [])
    except Exception:
        controls["rationale"] = controls["reason"]
        controls["policy_sections_cited"] = [c["section"] for c in citations[:3]]
    return controls


# ======================================================================== #
# Approval Router (deterministic)
# ======================================================================== #
def approval_router_agent(intake: dict) -> list[str]:
    value = intake.get("contract_value_inr") or 0
    access = {str(a).lower() for a in intake.get("requested_access", [])}
    legal_docs = intake.get("legal_documents", [])
    approvers: list[str] = []

    # financial
    if value > config.THRESHOLD_CFO:
        approvers += ["Finance Manager", "CFO"]
    elif value > config.THRESHOLD_FINANCE:
        approvers.append("Finance Manager")
    elif value > config.THRESHOLD_MANAGER:
        approvers.append("Department Head")
    elif value > 0:
        approvers.append("Manager")

    # functional
    if legal_docs:
        approvers.append("Legal Reviewer")
    if access:
        approvers.append("Security Reviewer")
    if access & {"customer_pii", "customer_analytics"}:
        approvers.append("Data Protection Officer")
    # every new vendor needs a business owner sign-off
    if intake.get("workflow_type") == "vendor_onboarding" or intake.get("vendor_name"):
        approvers.append("Business Owner")

    # dedupe, preserve order
    seen: set[str] = set()
    ordered = []
    for a in approvers:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered


# ======================================================================== #
# Document generation
# ======================================================================== #
def document_agent(intake: dict, controls: dict, approvers: list[str]) -> list[dict]:
    docs: list[dict] = []
    vendor = intake.get("vendor_name") or "the vendor"

    # Vendor onboarding form (structured, deterministic)
    docs.append({
        "doc_type": "vendor_onboarding_form",
        "title": f"Vendor Onboarding Packet — {vendor}",
        "content": json.dumps({
            "vendor_name": vendor,
            "department": intake.get("department"),
            "contract_value_inr": intake.get("contract_value_inr"),
            "requested_access": intake.get("requested_access"),
            "business_owner": intake.get("business_owner"),
            "risk_level": controls["risk_level"],
            "required_approvals": approvers,
            "missing_fields": intake.get("missing_fields", []),
        }, indent=2, ensure_ascii=False),
    })

    # NDA draft (LLM if a legal doc requested)
    if intake.get("legal_documents"):
        try:
            nda = chat([
                {"role": "system", "content": "You draft concise enterprise NDA request summaries. 120 words max."},
                {"role": "user", "content": f"Draft an NDA request summary for vendor '{vendor}' "
                 f"(department {intake.get('department')}). It must state that access is blocked until the "
                 f"NDA is executed by Legal. Do not claim it is signed."},
            ], reasoning=False, max_tokens=300)
        except Exception:
            nda = f"NDA request for {vendor}: standard mutual NDA (LEG-TMPL-NDA-STD). Routed to Legal. Access blocked until executed."
        docs.append({"doc_type": "nda_request", "title": f"NDA Request — {vendor}", "content": nda})

    # Approval summary
    docs.append({
        "doc_type": "approval_summary",
        "title": f"Approval Summary — {vendor}",
        "content": f"Risk: {controls['risk_level']}\nRequired approvals: {', '.join(approvers) or 'none'}\n"
                   f"Blocked until approved: {', '.join(controls['blocked_actions']) or 'none'}\n"
                   f"Rationale: {controls.get('rationale', controls['reason'])}",
    })
    return docs


# ======================================================================== #
# Tool execution (with per-tool safety gate)
# ======================================================================== #
def tool_execution_agent(workflow_id: str, intake: dict, controls: dict) -> list[dict]:
    vendor = intake.get("vendor_name") or "Unknown Vendor"
    results: list[dict] = []

    def run(tool_name: str, fn, input_obj: dict):
        risk, needs_human = mock_services.TOOL_RISK.get(tool_name, ("medium", True))
        blocked = tool_name in controls["blocked_actions"] or needs_human
        if blocked:
            output = {
                "decision": "block",
                "requires_human_approval": True,
                "approval_status": "pending",
                "note": f"{tool_name} is {risk}-risk and cannot be auto-executed.",
            }
            db.add_tool_call(workflow_id, tool_name, input_obj, output, risk, True, "blocked")
            db.add_audit(workflow_id, "tool_blocked", f"Blocked {tool_name} — requires human approval", output)
            results.append({"tool": tool_name, "status": "blocked", **output})
            return
        output = fn()
        db.add_tool_call(workflow_id, tool_name, input_obj, output, risk, False, "executed")
        db.add_audit(workflow_id, "tool_executed", f"Executed {tool_name} -> {output.get('ticket_id','ok')}", output)
        results.append({"tool": tool_name, "status": "executed", **output})

    # Safe, auto-allowed actions
    if intake.get("contract_value_inr"):
        run("create_purchase_request",
            lambda: mock_services.create_purchase_request(
                workflow_id, vendor, intake["contract_value_inr"],
                intake.get("department") or "Unspecified",
                intake.get("intent_summary", "vendor engagement")),
            {"vendor": vendor, "amount_inr": intake["contract_value_inr"]})

    if intake.get("legal_documents"):
        run("create_legal_review",
            lambda: mock_services.create_legal_review(workflow_id, vendor, "NDA"),
            {"vendor": vendor, "doc_type": "NDA"})

    if intake.get("requested_access"):
        run("create_security_review",
            lambda: mock_services.create_security_review(workflow_id, vendor, intake["requested_access"]),
            {"vendor": vendor, "scope": intake["requested_access"]})
        run("create_access_request",
            lambda: mock_services.create_access_request(
                workflow_id, vendor, intake["requested_access"], intake.get("access_duration_days")),
            {"vendor": vendor, "access": intake["requested_access"]})

    # High-risk effect the model might be tempted to take — always blocked:
    high_risk_access = {str(a).lower() for a in intake.get("requested_access", [])} & config.HIGH_RISK_ACCESS
    if high_risk_access:
        run("grant_production_access",
            lambda: {"provisioned": True},  # never actually runs (blocked)
            {"vendor": vendor, "access": sorted(high_risk_access)})

    # Notify approvers
    run("send_notification",
        lambda: mock_services.send_notification(workflow_id, "approvers", f"Approvals requested for {vendor}"),
        {"to": "approvers"})

    return results


# ======================================================================== #
# Audit
# ======================================================================== #
def audit_agent(workflow_id: str) -> dict:
    events = db.list_audit(workflow_id)
    timeline = [
        {"time": e["created_at"], "event_type": e["event_type"], "message": e["message"]}
        for e in events
    ]
    return {"workflow_id": workflow_id, "timeline": timeline, "event_count": len(timeline)}

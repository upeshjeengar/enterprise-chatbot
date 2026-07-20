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
import re
from typing import Any

from . import config, db, guardrails, mock_services
from .llm_gateway import chat, chat_json
from .rag import get_store


# ======================================================================== #
# Intent routing (deterministic-first)
# ======================================================================== #
# Informational lead-ins: if a message opens this way it is a question about
# policy/process, NOT a request to run a workflow — even if it mentions an
# action verb like "onboard" (e.g. "how do I onboard a vendor?").
_QUESTION_LEAD = re.compile(
    r"^\s*(what('?s| is| are)?|how|when|where|who(m|se)?|why|which|"
    r"explain|tell me|list|describe|define|summarize|"
    r"do i|does|did|is there|are there|is it|can i|could i|should i|"
    r"need i|do we|are we|will i)\b",
    re.IGNORECASE,
)
# Verbs that indicate the user wants the agent to *perform* a workflow.
_ACTION_VERB = re.compile(
    r"\b(onboard|provision|grant|register|set ?up|create (a|an|the|purchase)|"
    r"process (a |the )?payment|purchase|procure|approve (the|this|a )|"
    r"add (a |an |the )?(vendor|supplier)|give .* access)\b",
    re.IGNORECASE,
)


def classify_intent(user_text: str) -> str:
    """Return "question" (informational Q&A) or "action" (run a workflow).

    Deterministic and explainable so routing never silently flips on model
    variance. Priority: informational lead-ins win first (they are almost
    always questions), then explicit action verbs, then a trailing "?".
    """
    t = user_text.strip()
    if _QUESTION_LEAD.match(t):
        return "question"
    if _ACTION_VERB.search(t):
        return "action"
    if t.endswith("?"):
        return "question"
    return "action"  # bare imperatives ("Onboard Acme …") default to action


# ======================================================================== #
# Policy Q&A (RAG answer path — no workflow record)
# ======================================================================== #
def policy_qa_agent(question: str) -> dict:
    """Answer an informational policy question, grounded in retrieved policy."""
    store = get_store()
    citations = store.retrieve(question, k=5)
    if not citations:
        return {
            "answer": "Policy documents aren't ingested yet, so I can't ground an "
            "answer. Click “Ingest policies” and ask again.",
            "citations": [],
        }
    cite_txt = "\n\n".join(
        f"[{c['section']}] (score {c['relevance_score']})\n{c['text'][:600]}"
        for c in citations
    )
    prompt = f"""Answer the employee's policy question using ONLY the retrieved policy
excerpts below. Quote concrete thresholds/roles verbatim when present. If the answer
is not contained in the excerpts, say you don't have a policy that covers it. Cite the
section name(s) you used. Keep it under 120 words.

Question: {question}

Retrieved policy excerpts:
{cite_txt}
"""
    try:
        answer = chat(
            [
                {"role": "system", "content": "detailed thinking off"},
                {"role": "user", "content": prompt},
            ],
            reasoning=True, max_tokens=400, timeout=30.0, retries=2,
        )
    except Exception:
        answer = None
    if not answer or not str(answer).strip():
        # LLM unavailable or returned empty — ground on the top retrieved excerpt.
        top = citations[0]
        answer = (
            f"Based on {top['section']}: {top['text'][:400]}"
        )
    return {"answer": str(answer).strip(), "citations": citations}


# ======================================================================== #
# Intake
# ======================================================================== #
def intake_agent(user_text: str) -> dict:
    prompt = f"""Extract a structured enterprise workflow request from the employee message.
Classify what the employee actually wants to DO — do not assume it is a vendor onboarding.

Return JSON with exactly these keys:
  workflow_type: choose the single best match:
    - "notification"        : post/send a message or alert (e.g. Slack channel, email blast)
    - "project_task"        : create a tracking/task ticket (e.g. Jira story/task/epic)
    - "it_service_request"  : IT/hardware/service request (e.g. ServiceNow ITSM, laptop, onboarding hardware)
    - "it_access_request"   : grant/request access to an app, system, or data (e.g. Okta group, GitHub)
    - "software_license"    : buy software licenses/subscriptions
    - "procurement"         : purchase goods/services / raise a purchase requisition
    - "vendor_onboarding"   : formally onboard/register a NEW vendor or supplier
    - "general_request"     : none of the above
    Pick by the primary action verb and the target system named. "Log a ServiceNow ticket" -> it_service_request.
    "Create a Jira task to track X" -> project_task. "Send a Slack notification" -> notification.
    "Grant Okta access" -> it_access_request. Only use vendor_onboarding when a NEW vendor is being set up.
  target_system: the named system if any (e.g. "ServiceNow","Jira","Slack","SAP Ariba","Okta","DocuSign") or null
  vendor_name: string or null — a third-party company name if one is central to the request, else null
  department: string or null — the sponsoring team/department if stated (e.g. "for Marketing","for HR","'MKTG' project"). null if not stated. Do NOT invent one.
  contract_value_inr: integer rupees or null (convert lakh: 1 lakh = 100000; crore: 1 crore = 10000000). null if no money is mentioned.
  requested_access: array of short access identifiers (e.g. ["analytics_dashboard","github","finance_reporting"]) or []
  legal_documents: array (e.g. ["NDA"]) or []
  business_owner: string or null
  access_duration_days: integer or null
  subject: string or null — the person/asset/topic the action is about (e.g. "Priya Sharma","developer hardware","contract renewal deadline")
  intent_summary: one short sentence describing the request

Employee message:
\"\"\"{user_text}\"\"\"
"""
    try:
        data = chat_json([{"role": "user", "content": prompt}], reasoning=False)
    except Exception:
        data = {}
    # normalize
    data.setdefault("workflow_type", config.DEFAULT_WORKFLOW_TYPE)
    if data["workflow_type"] not in config.WORKFLOW_PROFILES:
        data["workflow_type"] = config.DEFAULT_WORKFLOW_TYPE
    data.setdefault("target_system", None)
    data.setdefault("vendor_name", None)
    data.setdefault("department", None)
    data.setdefault("contract_value_inr", None)
    data.setdefault("requested_access", [])
    data.setdefault("legal_documents", [])
    data.setdefault("business_owner", None)
    data.setdefault("access_duration_days", None)
    data.setdefault("subject", None)
    data.setdefault("intent_summary", user_text[:120])
    # coerce types
    if isinstance(data["requested_access"], str):
        data["requested_access"] = [data["requested_access"]] if data["requested_access"] else []
    if isinstance(data["legal_documents"], str):
        data["legal_documents"] = [data["legal_documents"]] if data["legal_documents"] else []
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
    profile = config.profile_for(intake.get("workflow_type"))
    spend_gate = profile["spend_gate"]
    # A value only drives financial risk/approval when the workflow is actually
    # a spend action. A Jira task that merely mentions "₹1 lakh budget" is not a
    # purchase, so its value is recorded but does not trigger finance approval.
    raw_value = intake.get("contract_value_inr") or 0
    value = raw_value if spend_gate else 0
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
        "create_project_task",
        "create_itsm_ticket",
        "send_notification",
    ]

    reasons = []
    if value > config.THRESHOLD_CFO:
        reasons.append(f"Contract ₹{value:,} exceeds ₹10 lakh — CFO co-approval required.")
    elif value > config.THRESHOLD_FINANCE:
        reasons.append(f"Contract ₹{value:,} exceeds ₹5 lakh — Finance approval required.")
    elif value > config.THRESHOLD_MANAGER:
        reasons.append(f"Contract ₹{value:,} is ₹2–5 lakh — Department Head approval required.")
    elif value > 0:
        reasons.append(f"Contract ₹{value:,} is under ₹2 lakh — Manager approval required.")
    if sensitive_access:
        reasons.append(f"Access {sorted(sensitive_access)} may expose customer/analytics data — Security approval required, provisioning blocked until approved.")
    if high_risk_access:
        reasons.append(f"Access {sorted(high_risk_access)} is production/admin class — an AI agent may never grant it; hard-blocked.")
    if legal_docs:
        reasons.append(f"Legal document(s) {sorted(legal_docs)} requested — Legal review required before execution.")
    if not reasons:
        reasons.append(f"Low-risk {profile['label'].lower()} within auto-allowed bounds.")

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
    profile = config.profile_for(intake.get("workflow_type"))
    value = (intake.get("contract_value_inr") or 0) if profile["spend_gate"] else 0
    access = {str(a).lower() for a in intake.get("requested_access", [])}
    legal_docs = intake.get("legal_documents", [])
    approvers: list[str] = []

    # financial — only when this workflow actually represents spend
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
    # business-owner sign-off only where the profile calls for it (vendor onboarding)
    if profile["needs_business_owner"]:
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
    profile = config.profile_for(intake.get("workflow_type"))
    docs: list[dict] = []
    subject = intake.get("vendor_name") or intake.get("subject") or "request"

    # A request summary tailored to what was asked (vendor packet only for vendors).
    if profile["needs_business_owner"]:  # vendor onboarding -> full packet
        docs.append({
            "doc_type": "vendor_onboarding_form",
            "title": f"Vendor Onboarding Packet — {subject}",
            "content": json.dumps({
                "vendor_name": intake.get("vendor_name"),
                "department": intake.get("department"),
                "contract_value_inr": intake.get("contract_value_inr"),
                "requested_access": intake.get("requested_access"),
                "business_owner": intake.get("business_owner"),
                "risk_level": controls["risk_level"],
                "required_approvals": approvers,
            }, indent=2, ensure_ascii=False),
        })
    else:
        docs.append({
            "doc_type": "request_summary",
            "title": f"{profile['label']} — {subject}",
            "content": json.dumps({
                "workflow_type": intake.get("workflow_type"),
                "target_system": intake.get("target_system"),
                "subject": intake.get("subject"),
                "department": intake.get("department"),
                "contract_value_inr": intake.get("contract_value_inr"),
                "requested_access": intake.get("requested_access"),
                "risk_level": controls["risk_level"],
                "required_approvals": approvers,
                "intent": intake.get("intent_summary"),
            }, indent=2, ensure_ascii=False),
        })

    # NDA draft (LLM if a legal doc requested)
    if intake.get("legal_documents"):
        try:
            nda = chat([
                {"role": "system", "content": "You draft concise enterprise NDA request summaries. 120 words max."},
                {"role": "user", "content": f"Draft an NDA request summary for vendor '{subject}' "
                 f"(department {intake.get('department')}). It must state that access is blocked until the "
                 f"NDA is executed by Legal. Do not claim it is signed."},
            ], reasoning=False, max_tokens=300)
        except Exception:
            nda = f"NDA request for {subject}: standard mutual NDA (LEG-TMPL-NDA-STD). Routed to Legal. Access blocked until executed."
        docs.append({"doc_type": "nda_request", "title": f"NDA Request — {subject}", "content": nda})

    # Approval summary — only meaningful when approvals are required.
    if approvers or controls["risk_level"] != "low":
        docs.append({
            "doc_type": "approval_summary",
            "title": f"Approval Summary — {subject}",
            "content": f"Risk: {controls['risk_level']}\nRequired approvals: {', '.join(approvers) or 'none'}\n"
                       f"Blocked until approved: {', '.join(controls['blocked_actions']) or 'none'}\n"
                       f"Rationale: {controls.get('rationale', controls['reason'])}",
        })
    return docs


# ======================================================================== #
# Tool execution (with per-tool safety gate)
# ======================================================================== #
def tool_execution_agent(workflow_id: str, intake: dict, controls: dict) -> list[dict]:
    wtype = intake.get("workflow_type") or config.DEFAULT_WORKFLOW_TYPE
    vendor = intake.get("vendor_name") or intake.get("subject") or "Unknown"
    summary = intake.get("intent_summary", "request")
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

    # ---- low-touch actions: fire exactly the tool the user asked for -------
    if wtype == "notification":
        channel = intake.get("subject") or intake.get("target_system") or "channel"
        run("send_notification",
            lambda: mock_services.send_notification(workflow_id, str(channel), summary),
            {"to": channel, "message": summary})
        return results

    if wtype == "project_task":
        project = intake.get("department") or "TASK"
        run("create_project_task",
            lambda: mock_services.create_project_task(workflow_id, str(project), summary),
            {"project": project, "summary": summary})
        return results

    if wtype == "it_service_request":
        run("create_itsm_ticket",
            lambda: mock_services.create_itsm_ticket(workflow_id, summary),
            {"short_description": summary})
        return results

    if wtype == "it_access_request":
        access = intake.get("requested_access") or []
        run("create_security_review",
            lambda: mock_services.create_security_review(workflow_id, vendor, access),
            {"subject": vendor, "scope": access})
        run("create_access_request",
            lambda: mock_services.create_access_request(
                workflow_id, vendor, access, intake.get("access_duration_days")),
            {"subject": vendor, "access": access})
        _run_high_risk_guard(run, intake)
        return results

    # ---- procurement / vendor / software / general: spend + legal + access -
    if intake.get("contract_value_inr"):
        run("create_purchase_request",
            lambda: mock_services.create_purchase_request(
                workflow_id, vendor, intake["contract_value_inr"],
                intake.get("department") or "Unspecified", summary),
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

    _run_high_risk_guard(run, intake)

    # Notify approvers only when there is an approval workflow to notify about.
    run("send_notification",
        lambda: mock_services.send_notification(workflow_id, "approvers", f"Approvals requested for {vendor}"),
        {"to": "approvers"})

    return results


def _run_high_risk_guard(run, intake: dict) -> None:
    """A production/admin access grant the model might attempt — always blocked."""
    high_risk_access = {str(a).lower() for a in intake.get("requested_access", [])} & config.HIGH_RISK_ACCESS
    if high_risk_access:
        run("grant_production_access",
            lambda: {"provisioned": True},  # never actually runs (blocked)
            {"subject": intake.get("vendor_name") or intake.get("subject"),
             "access": sorted(high_risk_access)})


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

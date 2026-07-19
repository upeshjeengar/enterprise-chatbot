"""Workflow Orchestrator Agent — the supervisor. Runs the multi-agent pipeline,
enforces guardrails at each boundary, drives the workflow state machine, and
persists every decision as an audit event.

State machine (spec section 9):
  DRAFT -> INFO_REQUIRED -> POLICY_CHECKED -> APPROVALS_PENDING -> APPROVED
        -> TOOLS_EXECUTED -> COMPLETED
  failure states: REJECTED, POLICY_BLOCKED, NEEDS_HUMAN_REVIEW, TOOL_FAILED
"""
from __future__ import annotations

from . import agents, db, guardrails


def run_workflow(user_text: str, requester: str = "employee@acmecorp.example",
                 injected_document: str | None = None) -> dict:
    # --- Guardrail Layer 1: input rail --------------------------------------
    inp = guardrails.input_rail(user_text)

    # Intake first so we always have a workflow record to attach audit to.
    intake = agents.intake_agent(user_text)
    wid = db.create_workflow({
        "workflow_type": intake.get("workflow_type", "vendor_onboarding"),
        "requester": requester,
        "department": intake.get("department"),
        "vendor_name": intake.get("vendor_name"),
        "contract_value_inr": intake.get("contract_value_inr"),
        "status": "DRAFT",
        "summary": intake.get("intent_summary"),
        "payload": intake,
    })
    db.add_audit(wid, "request_received", f"Request received from {requester}", {"text": user_text})
    db.add_audit(wid, "intake_parsed",
                 f"Parsed as {intake.get('workflow_type')} for vendor {intake.get('vendor_name')}", intake)

    if not inp.allowed:
        db.update_workflow(wid, status="POLICY_BLOCKED", risk_level="high")
        db.add_audit(wid, "guardrail_input_block", inp.reason, {"matched": inp.matched})
        return _snapshot(wid, note=inp.reason, blocked=True)

    # --- Guardrail Layer 2: retrieval / injection rail (vendor doc) ----------
    if injected_document:
        det = guardrails.retrieval_rail(injected_document)
        if not det.allowed:
            db.add_audit(wid, "guardrail_injection_block", det.reason, {"matched": det.matched})

    # --- Missing info check -> INFO_REQUIRED --------------------------------
    critical = _critical_missing(intake)
    if critical:
        db.update_workflow(wid, status="INFO_REQUIRED")
        q = _followup_question(critical)
        db.add_audit(wid, "info_required", q, {"missing": critical})
        snap = _snapshot(wid, note=q)
        snap["followup_question"] = q
        snap["missing_fields"] = critical
        return snap

    # --- Plan ---------------------------------------------------------------
    plan = agents.planning_agent(intake)
    db.add_audit(wid, "plan_created", f"Planned {len(plan)} steps", {"plan": plan})

    # --- Policy RAG ---------------------------------------------------------
    citations = agents.policy_rag_agent(intake, user_text)
    db.add_citations(wid, citations)
    db.add_audit(wid, "policy_retrieved",
                 f"Retrieved {len(citations)} relevant policy sections",
                 {"sections": [c["section"] for c in citations]})

    # --- Compliance & Risk --------------------------------------------------
    controls = agents.compliance_agent(intake, citations)
    db.update_workflow(wid, status="POLICY_CHECKED", risk_level=controls["risk_level"])
    db.add_audit(wid, "risk_evaluated",
                 f"Risk={controls['risk_level']}. {controls.get('rationale','')}", controls)

    # --- Approval routing ---------------------------------------------------
    approvers = agents.approval_router_agent(intake)
    for role in approvers:
        db.add_approval(wid, role)
    db.update_workflow(wid, status="APPROVALS_PENDING")
    db.add_audit(wid, "approvals_routed",
                 f"Routed approvals to: {', '.join(approvers) or 'none'}", {"approvers": approvers})

    # --- Documents ----------------------------------------------------------
    docs = agents.document_agent(intake, controls, approvers)
    for d in docs:
        db.add_document(wid, d["doc_type"], d["title"], d["content"])
    db.add_audit(wid, "documents_generated", f"Generated {len(docs)} documents",
                 {"docs": [d["title"] for d in docs]})

    # --- Tool execution (safe only; risky blocked) --------------------------
    tool_results = agents.tool_execution_agent(wid, intake, controls)
    executed = [t for t in tool_results if t["status"] == "executed"]
    blocked = [t for t in tool_results if t["status"] == "blocked"]
    db.update_workflow(wid, status="TOOLS_EXECUTED")
    db.add_audit(wid, "tools_done",
                 f"Executed {len(executed)} safe tools, blocked {len(blocked)} high-risk tools",
                 {"executed": [t["tool"] for t in executed], "blocked": [t["tool"] for t in blocked]})

    # --- Final status -------------------------------------------------------
    # If there are pending approvals, we hold at APPROVALS_PENDING (human-in-loop).
    final = "APPROVALS_PENDING" if approvers else "COMPLETED"
    db.update_workflow(wid, status=final)
    db.add_audit(wid, "audit_report", "Generated audit report", {"final_status": final})

    return _snapshot(wid, note=controls.get("rationale", controls["reason"]))


def _critical_missing(intake: dict) -> list[str]:
    missing = []
    if not intake.get("vendor_name") and intake.get("workflow_type") in ("vendor_onboarding", "procurement"):
        missing.append("vendor_name")
    if intake.get("contract_value_inr") in (None, 0) and intake.get("workflow_type") in ("vendor_onboarding", "procurement"):
        missing.append("contract_value_inr")
    if not intake.get("department"):
        missing.append("department")
    return missing


def _followup_question(missing: list[str]) -> str:
    labels = {
        "vendor_name": "vendor name",
        "contract_value_inr": "contract value (INR)",
        "department": "sponsoring department",
        "business_owner": "business owner",
        "requested_access": "type of access needed",
    }
    pretty = ", ".join(labels.get(m, m) for m in missing)
    return f"Before I can proceed I need a few details: {pretty}. Could you provide them?"


def continue_after_approvals(wid: str) -> dict:
    """Called once all approvals are granted — advance to APPROVED/COMPLETED."""
    approvals = db.list_approvals(wid)
    if not approvals:
        db.update_workflow(wid, status="COMPLETED")
    elif all(a["status"] == "approved" for a in approvals):
        db.update_workflow(wid, status="APPROVED")
        db.add_audit(wid, "all_approved", "All required approvals granted", {})
        db.update_workflow(wid, status="COMPLETED")
        db.add_audit(wid, "completed", "Workflow completed; access may now be provisioned by IAM owner", {})
    elif any(a["status"] == "rejected" for a in approvals):
        db.update_workflow(wid, status="REJECTED")
        db.add_audit(wid, "rejected", "An approver rejected the request", {})
    return _snapshot(wid)


def _snapshot(wid: str, note: str = "", blocked: bool = False) -> dict:
    wf = db.get_workflow(wid)
    return {
        "workflow_id": wid,
        "workflow": wf,
        "status": wf["status"] if wf else "UNKNOWN",
        "risk_level": wf.get("risk_level") if wf else None,
        "approvals": db.list_approvals(wid),
        "tool_calls": db.list_tool_calls(wid),
        "citations": db.list_citations(wid),
        "documents": db.list_documents(wid),
        "audit": db.list_audit(wid),
        "note": note,
        "blocked": blocked,
    }


def get_snapshot(wid: str) -> dict:
    return _snapshot(wid)

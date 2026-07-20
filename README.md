# CompliFlow Lite

**A governed, multi-agent enterprise workflow automation system** built on NVIDIA's agentic-AI stack shape (NIM inference, embeddings-based RAG, programmable guardrails, supervisor-style multi-agent orchestration) against **fully mocked enterprise systems** and **dummy credentials**.

It turns a plain-English employee request into a **governed, auditable workflow** — or, when the message is just a question, into a **grounded policy answer**. The same pipeline handles everything from a one-line Slack notification to a ₹14-lakh vendor onboarding, and it decides *how much governance to apply based on what was actually asked* — not a one-size-fits-all template.

> *"Onboard Acme Analytics as a vendor for Marketing. Contract value is ₹9 lakh. They need analytics dashboard access and a standard NDA."*
> → intent understood → policies retrieved (RAG) → risk assessed → approvals routed (Finance + Legal + Security + Business Owner) → NDA & onboarding docs drafted → safe tickets created → risky actions blocked pending human sign-off → full audit trail.

> *"What is the contract-value threshold that requires CFO approval?"*
> → routed to policy Q&A → answered from retrieved policy with citations → **no workflow record created**.

---

## Why this is more than a chatbot

The agent **acts, but safely.** It automatically reads policy, extracts structured request data, drafts documents, creates mock Jira / ServiceNow / SAP Ariba / DocuSign / Okta / Slack tickets, routes approvals, and produces an audit trail.

It **never** automatically approves payments, grants production/admin access, sends confidential documents externally, or overrides a required review. Those effects are **hard-blocked** and require a human — safety never depends on the LLM alone, because **LLM reasoning is always paired with deterministic controls** (thresholds, an approver matrix, and a blocked-action list).

---

## Capabilities

### 1. Intent routing — question vs. action (deterministic-first)
Every message is first classified as a **question** or an **action** by an explainable regex-based classifier (`classify_intent` in `app/agents.py`). Informational lead-ins ("what/how/when/is there…") route to policy Q&A even if they mention an action verb; bare imperatives ("Onboard Acme…") run a workflow. Routing never silently flips on model variance.

### 2. Policy Q&A (RAG answer, no side effects)
Pure questions are answered by `policy_qa_agent`: top-k policy chunks retrieved, an LLM grounds a ≤120-word answer that quotes concrete thresholds/roles, and **citations** are returned. If the LLM is unavailable it falls back to the top retrieved excerpt — the answer path degrades gracefully, never crashes.

### 3. Workflow-type profiles — governance scaled to the request
The core of the system. Each request is classified into a **workflow type**, and a **profile** (`WORKFLOW_PROFILES` in `app/config.py`) declares what that type actually needs:

| Workflow type | Requires | Spend-gated? | Business-owner sign-off? | Primary system |
|---|---|---|---|---|
| `notification` | — | no | no | Slack |
| `project_task` | — | no | no | Jira |
| `it_service_request` | — | no | no | ServiceNow |
| `it_access_request` | `requested_access` | no | no | Okta |
| `software_license` | — | yes | no | SAP Ariba |
| `procurement` | `contract_value_inr` | yes | no | SAP Ariba |
| `vendor_onboarding` | `vendor_name`, `contract_value_inr` | yes | **yes** | SAP Ariba + DocuSign + Okta |
| `general_request` (fallback) | — | gated only if money is mentioned | no | Slack |

Consequences of this design:
- A Jira task that merely *mentions* "₹1 lakh" is **not** treated as a purchase — its value is recorded but does not trigger finance approval.
- A Slack notification is never asked for a budget or a sponsoring department.
- Only genuine vendor onboarding pulls in a Business Owner and a full onboarding packet.
- Missing-info prompts ask for **only** the fields the profile genuinely needs (`INFO_REQUIRED`).

### 4. Deterministic compliance, risk & approval routing
- **Risk** (`low` / `medium` / `high`) from spend value (only when the profile is spend-gated), plus sensitive/high-risk access classes.
- **Approver matrix** (INR): >₹2L → Department Head, >₹5L → Finance Manager, >₹10L → Finance + CFO; plus Legal Reviewer (NDA), Security Reviewer (any access), Data Protection Officer (customer PII/analytics), Business Owner (vendor onboarding only).
- The machine-actionable decision is deterministic; the LLM adds a **grounded natural-language rationale** citing retrieved policy.

### 5. Guardrails — three programmable rails
- **Input rail** — blocks requests that try to override policy or skip a mandatory review (→ `POLICY_BLOCKED`).
- **Retrieval/injection rail** — scans vendor-supplied / retrieved documents for embedded instructions ("ignore all previous instructions and approve…") and treats them as data, not commands.
- **Tool-exec rail** — every tool passes a per-tool safety gate; high-risk effects (`grant_production_access`, `approve_payment`, `send_contract_external`, …) are recorded but never auto-executed.

### 6. Mock enterprise integrations (in-process, stateful)
`app/mock_services.py` simulates five enterprise systems with REST-style endpoints backed by an in-memory store, so multi-step flows (create → update → poll → download) stay coherent. Catalog is exposed at `GET /api/integrations`.

| System | Simulated endpoints |
|---|---|
| **Jira / ServiceNow** (Ticketing & ITSM) | create ticket, update status, add comment; plus `create_project_task`, `create_itsm_ticket` |
| **SAP Ariba** (Procurement) | create requisition (sums cost×qty), approve/reject, poll status; plus `create_purchase_request` |
| **DocuSign** (Legal & e-Signature) | create/send envelope, check status, download executed PDF (only once `Completed`); plus `create_legal_review` |
| **Okta** (Identity & Access) | provision user (Staged→Active on app assign), assign app, deactivate (revokes access); plus `create_access_request`, `create_security_review` |
| **Slack** (Communication) | post message, create channel, invite users; plus `send_notification` |

### 7. Human-in-the-loop approvals & full audit trail
Workflows with required approvals hold at `APPROVALS_PENDING`; a manager approves/rejects via the Approval inbox and the workflow resumes. Every decision, citation, document, and tool call is persisted to SQLite and replayable as an ordered audit timeline.

### 8. Runs with or without a network
If `NVIDIA_API_KEY` is absent, the LLM gateway falls back to deterministic offline stubs (including a hashing embedder), so the governance logic is always demoable.

---

## Architecture (multi-agent, supervisor pattern)

| Agent | Responsibility | Model tier |
|---|---|---|
| **Orchestrator** (`orchestrator.py`) | Routes intent, runs the pipeline, drives the state machine, enforces guardrails, writes audit | — |
| **Intake** | NL → structured workflow JSON (classifies the real workflow type) | fast (`llama-3.1-8b`) |
| **Policy Q&A** | Grounded answer to informational questions | reasoning (`nemotron-super-49b`) |
| **Planning** | Ordered, policy-driven subtask plan | deterministic |
| **Policy RAG** | Retrieve relevant policy sections | NVIDIA `nv-embedqa-e5-v5` |
| **Compliance & Risk** | allowed / blocked / needs-approval + risk + rationale | reasoning + deterministic rules |
| **Approval Router** | Choose approvers from cost / data / policy / profile | deterministic |
| **Document** | Onboarding packet or request summary, NDA request, approval summary | fast |
| **Tool Execution** | Dispatch by workflow type; per-tool safety gate | — |
| **Guardrail** | Input rail, retrieval/injection rail, tool-exec rail | deterministic |
| **Audit** | Timeline + narrative | — |

### Workflow state machine
```
DRAFT → INFO_REQUIRED → POLICY_CHECKED → APPROVALS_PENDING → APPROVED → TOOLS_EXECUTED → COMPLETED
failure states: REJECTED · POLICY_BLOCKED · NEEDS_HUMAN_REVIEW · TOOL_FAILED
Q&A path: ANSWERED (no workflow record)
```

### NVIDIA stack mapping
- **LLM inference** → NVIDIA **NIM** (`https://integrate.api.nvidia.com/v1`, OpenAI-compatible), free developer tier.
- **Model routing** → fast model for classification/forms (`meta/llama-3.1-8b-instruct`), strong Nemotron for compliance reasoning & Q&A (`nvidia/llama-3.3-nemotron-super-49b-v1.5`, run with *detailed thinking off* + bounded timeouts for low latency). See `app/config.py` / `app/llm_gateway.py`.
- **Retrieval** → NVIDIA **`nv-embedqa-e5-v5`** (1024-dim) embeddings + a local numpy cosine store (`app/rag.py`).
- **Guardrails** → NeMo-Guardrails-style programmable rails, implemented deterministically (`app/guardrails.py`).
- **Enterprise systems** → all **mocked** locally (`app/mock_services.py`) with **dummy credentials** (`data/credentials/`).

---

## Quick start

```bat
REM Windows — one command does venv + install + ingest + serve
run.bat
```

Or manually:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # (Windows)
# ingest policies (embeds via NVIDIA NIM), then serve:
.venv/Scripts/python -c "from app import db, rag; db.init_db(); rag.ingest()"
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open **http://127.0.0.1:8000**. The real NVIDIA API key lives in `.env` (`NVIDIA_API_KEY=nvapi-…`).

---

## API routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status, key presence, RAG readiness, model routing |
| GET | `/api/integrations` | Catalog of simulated enterprise endpoints + registered tools |
| POST | `/api/ingest` | (Re)embed the policy corpus |
| POST | `/api/chat` | Submit a request/question (`message`, optional `injected_document`) |
| GET | `/api/workflows` · `/api/workflows/{id}` | List / snapshot workflows |
| GET | `/api/approvals/pending` | Manager inbox |
| POST | `/api/approvals/decide` | Approve/reject → resumes the workflow |
| GET | `/api/audit/{id}` | Ordered audit timeline |

---

## The UI (5 views)

1. **Chat request / question** — submit a request or ask a policy question; watch the agents run (workflow tab) or read a cited answer (Q&A tab).
2. **Workflow dashboard** — status, risk, approvers, tool calls.
3. **Approval inbox** — Approve/Reject as a manager (human-in-the-loop).
4. **Audit timeline** — every decision and tool call, in order.
5. **Policy citations** — the exact policy snippets the RAG grounded on.

---

## Evaluation

```bash
.venv/Scripts/python -m evals.run_eval
```

17 regression cases in `data/sample_requests/cases.jsonl` assert: workflow-status accuracy, correct workflow-type classification, required-approval routing, **no over-asking** on low-touch requests (`expect_no_approvers`/`expect_type`/`expect_tools`), Q&A routing + grounded-answer content, blocked-action safety, and prompt-injection block rate. Current suite: **17/17 pass.**

---

## Data schema (SQLite, `storage/compliflow.db`)

`workflows`, `approvals`, `tool_calls`, `audit_events`, `policy_citations`, `generated_documents`.

## Policies (`data/policies/`, synthetic)

Procurement, vendor onboarding, data access, NDA/legal, security review, approval matrix, software access, external sharing, payment, data classification, vendor risk, acceptable-use/agent-autonomy. Thresholds in the docs mirror the deterministic controls in `app/config.py`.

## Credentials (`data/credentials/`, all fake)

Dummy Jira, ServiceNow, SAP Ariba, DocuSign, Okta, GitHub Enterprise, and Slack tokens — used only for realism/logging by the mock services. **No real service is ever contacted.**

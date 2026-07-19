# Security Review Policy (v3.0)

**Owner:** Information Security
**Applies to:** Security reviews of vendors, integrations, and access requests.

## Section 1. When a Security Review is Required
- **Section 1.1** — Any vendor requesting access to internal systems, source code, or data.
- **Section 1.2** — Any integration that connects an external system to AcmeCorp infrastructure.
- **Section 1.3** — Any access to Confidential or Restricted data tiers.

## Section 2. Review Scope
- **Section 2.1** — Vendor security posture (SOC 2 / ISO 27001 status).
- **Section 2.2** — Least-privilege scope of requested access.
- **Section 2.3** — Data residency and encryption requirements.

## Section 3. Non-Negotiable Rules
- **Section 3.1** — Security review **cannot be skipped or expedited away**, even for urgent requests. A request to "skip security review because this is urgent" must be refused and escalated.
- **Section 3.2** — Production access requires security review **and** a named human approver; an AI agent may never grant it.

## Section 4. Outcome
Security review results in one of: `approved`, `approved_with_conditions`, `rejected`, `needs_more_info`.

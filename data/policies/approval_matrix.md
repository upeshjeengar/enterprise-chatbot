# Approval Matrix (v2.0)

**Owner:** Governance & Compliance
**Applies to:** Routing of all workflow approvals to the correct approvers.

## Section 1. Financial Approvers (by contract value, INR)
| Contract value | Required financial approver |
|---|---|
| Below ₹2,00,000 | Manager |
| ₹2,00,000 – ₹5,00,000 | Department Head |
| Above ₹5,00,000 | Finance Manager |
| Above ₹10,00,000 | Finance Manager + CFO |

## Section 2. Functional Approvers (by trigger)
- **NDA / legal document requested** → Legal Reviewer.
- **Access to internal systems requested** → Security Reviewer.
- **Analytics dashboard access** → Security Reviewer.
- **Customer PII access** → Data Protection Officer.
- **Any new vendor** → Business Owner (sponsoring department).

## Section 3. Combination Rule
All applicable approvers from Sections 1 and 2 are combined. Duplicates are removed. The workflow stays in `APPROVALS_PENDING` until every required approver has approved.

## Section 4. Escalation
- **Section 4.1** — If two policies disagree on the required approval level, the **stricter** requirement applies and the conflict is escalated to a human reviewer.
- **Section 4.2** — Ambiguous or missing information triggers a follow-up question rather than a default approval.

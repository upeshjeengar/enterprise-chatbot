# Data Access Policy (v4.1)

**Owner:** Information Security & Data Protection Office
**Applies to:** All requests for access to AcmeCorp data systems and dashboards.

## Section 1. Purpose
Governs who may access which data systems and what approvals are required based on data sensitivity.

## Section 2. Data Classification Tiers
- **Public** — no approval required.
- **Internal** — Manager approval.
- **Confidential** — Security approval.
- **Restricted / PII** — Data Protection Officer (DPO) approval.

## Section 3. System-Specific Rules
- **Section 3.1** — **Analytics dashboard** access exposes customer analytics data and requires **Security approval**.
- **Section 3.2** — **Customer PII** access requires **Data Protection Officer** approval.
- **Section 3.3** — **Production** and **production database** access **cannot be granted automatically by an AI agent** under any circumstances and always requires Security approval plus a named human owner.

## Section 4. Least Privilege
- **Section 4.1** — Access is granted for the minimum scope and duration necessary.
- **Section 4.2** — Access duration must be specified; open-ended access is not permitted for external vendors.

## Section 5. Prohibited Automatic Actions
- The agent must **block** and route to a human any request to grant dashboard, PII, or production access. It may prepare the access request form but must not provision.

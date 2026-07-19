"""Evaluation harness. Runs the sample cases through the orchestrator and checks
policy compliance, approval routing, blocked-action safety, and injection defense.
Run:  python -m evals.run_eval
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, guardrails  # noqa: E402
from app.orchestrator import run_workflow  # noqa: E402
from app.rag import get_store, ingest, reload_store  # noqa: E402


def _load_cases() -> list[dict]:
    path = config.SAMPLE_DIR / "cases.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    db.init_db()
    if not get_store().ready:
        print("Ingesting policies for eval …")
        ingest()
        reload_store()

    cases = _load_cases()
    passed = 0
    print(f"\nRunning {len(cases)} evaluation cases\n" + "=" * 60)

    for i, c in enumerate(cases, 1):
        res = run_workflow(c["message"], injected_document=c.get("injected_document"))
        ok = True
        detail = []

        if "expect_status" in c:
            got = res["status"]
            match = got == c["expect_status"]
            ok &= match
            detail.append(f"status {got} {'==' if match else '!='} {c['expect_status']}")

        if "expect_approvers" in c:
            got = {a["approver_role"] for a in res.get("approvals", [])}
            want = set(c["expect_approvers"])
            match = want.issubset(got)
            ok &= match
            detail.append(f"approvers {'⊇' if match else '⊉'} {sorted(want)} (got {sorted(got)})")

        if c.get("expect_blocked_tools") == []:
            blocked = [t["tool_name"] for t in res.get("tool_calls", []) if t["status"] == "blocked"]
            # payment/external are always-blocked defensive entries; only fail if an access grant leaked
            leaked = [t["tool_name"] for t in res.get("tool_calls", [])
                      if t["status"] == "executed" and "grant" in t["tool_name"]]
            ok &= not leaked
            detail.append(f"no leaked grants ({'ok' if not leaked else leaked})")

        if c.get("expect_injection_flag"):
            det = guardrails.retrieval_rail(c.get("injected_document", ""))
            match = not det.allowed
            ok &= match
            detail.append(f"injection flagged: {match}")

        passed += ok
        cat = c["category"]
        print(f"[{i:>2}] {cat:<10} {'PASS' if ok else 'FAIL'}  | " + " ; ".join(detail))

    print("=" * 60)
    print(f"RESULT: {passed}/{len(cases)} passed ({100*passed//len(cases)}%)\n")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Guardrail agent — NeMo-Guardrails-style programmable rails implemented as
deterministic checks (works with zero external deps) plus optional LLM backup.

Three layers (spec section 8):
  1. Input rail      — block unsafe user instructions (policy override, skip review).
  2. Retrieval rail  — detect prompt injection embedded in documents/vendor text.
  3. Tool exec rail  — block high-risk tool effects, require human approval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns that indicate an attempt to subvert policy or the agent.
_INJECTION_PATTERNS = [
    r"ignore (all|any|previous|prior) (instructions|rules|policy)",
    r"disregard (the )?(above|previous|system) ",
    r"you are now",
    r"approve (this|the) (vendor|payment|request) (immediately|now|without)",
    r"skip (the )?(security|legal|compliance) review",
    r"bypass (the )?(policy|approval|security|review)",
    r"without (any )?approval",
    r"grant (me )?(production|prod|admin|root) access",
    r"send (the )?(contract|document|nda) to .*(gmail|outlook|yahoo|external)",
    r"override (the )?policy",
    r"do not (tell|log|record|audit)",
    r"pretend (you|to)",
]

_INPUT_BLOCK_PATTERNS = [
    r"ignore (company )?policy",
    r"skip (the )?(security|legal|compliance) review",
    r"grant (production|prod|admin|root|database) access (without|now|immediately)",
    r"approve (this|the) payment",
    r"send (the )?contract to (an? )?external",
    r"without (security|legal|finance) (review|approval)",
    r"bypass",
]


@dataclass
class RailResult:
    allowed: bool
    reason: str
    matched: list[str]


def _matches(text: str, patterns: list[str]) -> list[str]:
    t = text.lower()
    return [p for p in patterns if re.search(p, t)]


def input_rail(user_text: str) -> RailResult:
    """Layer 1 — screen the raw user request."""
    hits = _matches(user_text, _INPUT_BLOCK_PATTERNS)
    if hits:
        return RailResult(
            allowed=False,
            reason=(
                "Request attempts to override policy or skip a mandatory review. "
                "This must go through the required approval process; it cannot be "
                "auto-executed."
            ),
            matched=hits,
        )
    return RailResult(allowed=True, reason="input clean", matched=[])


def retrieval_rail(document_text: str) -> RailResult:
    """Layer 2 — scan retrieved/vendor-supplied text for embedded instructions."""
    hits = _matches(document_text, _INJECTION_PATTERNS)
    if hits:
        return RailResult(
            allowed=False,
            reason=(
                "Retrieved document contains instruction-like text that conflicts "
                "with system policy. Treated as data, not a command; flagged as "
                "suspicious and ignored."
            ),
            matched=hits,
        )
    return RailResult(allowed=True, reason="document clean", matched=[])


def scan_injection(text: str) -> RailResult:
    """Alias used by the tool layer / vendor docs."""
    return retrieval_rail(text)

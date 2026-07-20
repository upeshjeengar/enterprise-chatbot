"""Central configuration + model routing for CompliFlow Lite."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- NVIDIA NIM (OpenAI-compatible) ----
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()

# ---- Model routing: cheap/fast vs strong reasoning (spec section 12) ----
FAST_MODEL = os.getenv("FAST_MODEL", "meta/llama-3.1-8b-instruct")
REASONING_MODEL = os.getenv("REASONING_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# ---- Storage ----
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(exist_ok=True)
DB_PATH = STORAGE_DIR / "compliflow.db"
VECTOR_PATH = STORAGE_DIR / "policy_vectors.npz"

# ---- Data ----
DATA_DIR = BASE_DIR / "data"
POLICY_DIR = DATA_DIR / "policies"
SAMPLE_DIR = DATA_DIR / "sample_requests"
CREDENTIAL_DIR = DATA_DIR / "credentials"

# ---- Policy thresholds (INR) — deterministic controls, mirror the policy docs ----
THRESHOLD_MANAGER = 200_000       # below -> manager
THRESHOLD_DEPT_HEAD = 500_000     # 2L-5L -> department head
THRESHOLD_FINANCE = 500_000       # above -> finance
THRESHOLD_CFO = 1_000_000         # above -> CFO

# Access types that can never be auto-granted by the agent.
HIGH_RISK_ACCESS = {"production", "production_database", "prod_db", "payments", "root", "admin"}
SENSITIVE_ACCESS = {"analytics_dashboard", "customer_pii", "customer_analytics", "financials"}


# ---- Workflow-type profiles ------------------------------------------------
# The pipeline is multi-purpose: it handles low-touch actions (log a ticket,
# post a notification) as well as high-governance ones (onboard a vendor, grant
# access). Requirements/approvals must depend on WHAT was asked, not be a single
# vendor-onboarding template applied to everything. Each profile declares:
#   requires        : fields that genuinely block this action if absent
#   spend_gate      : does a contract/spend value trigger financial approval?
#   needs_business_owner : does a business-owner sign-off apply?
#   tool            : the primary mock system this request drives
#   label           : human-readable name
DEFAULT_WORKFLOW_TYPE = "general_request"

WORKFLOW_PROFILES: dict[str, dict] = {
    # --- low-touch operational actions (no budget / no department needed) ---
    "notification": {
        "label": "Notification / alert",
        "requires": [],
        "spend_gate": False,
        "needs_business_owner": False,
        "tool": "send_notification",
    },
    "project_task": {
        "label": "Project / tracking task",
        "requires": [],
        "spend_gate": False,          # spend only matters if the user states one
        "needs_business_owner": False,
        "tool": "create_project_task",
    },
    "it_service_request": {
        "label": "IT service request (ITSM)",
        "requires": [],
        "spend_gate": False,
        "needs_business_owner": False,
        "tool": "create_itsm_ticket",
    },
    # --- access governance ---
    "it_access_request": {
        "label": "IT access request",
        "requires": ["requested_access"],
        "spend_gate": False,
        "needs_business_owner": False,
        "tool": "create_access_request",
    },
    "software_license": {
        "label": "Software license request",
        "requires": [],
        "spend_gate": True,
        "needs_business_owner": False,
        "tool": "create_purchase_request",
    },
    # --- procurement / vendor (high governance) ---
    "procurement": {
        "label": "Procurement / purchase",
        "requires": ["contract_value_inr"],
        "spend_gate": True,
        "needs_business_owner": False,
        "tool": "create_purchase_request",
    },
    "vendor_onboarding": {
        "label": "Vendor onboarding",
        "requires": ["vendor_name", "contract_value_inr"],
        "spend_gate": True,
        "needs_business_owner": True,
        "tool": "create_purchase_request",
    },
    # --- fallback ---
    DEFAULT_WORKFLOW_TYPE: {
        "label": "General request",
        "requires": [],
        "spend_gate": True,           # if they mention money, gate it; else don't
        "needs_business_owner": False,
        "tool": "send_notification",
    },
}


def profile_for(workflow_type: str | None) -> dict:
    """Return the workflow profile, defaulting gracefully for unknown types."""
    return WORKFLOW_PROFILES.get(workflow_type or "", WORKFLOW_PROFILES[DEFAULT_WORKFLOW_TYPE])


def has_api_key() -> bool:
    return NVIDIA_API_KEY.startswith("nvapi-")

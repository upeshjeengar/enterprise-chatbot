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


def has_api_key() -> bool:
    return NVIDIA_API_KEY.startswith("nvapi-")

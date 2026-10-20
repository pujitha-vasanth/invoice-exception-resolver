"""All settings in one place. Values come from the .env file in the project root."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
POLICY_DIR = DATA_DIR / "policies"


def db_path() -> Path:
    """Where the SQLite database lives. Read on every call so tests can point it elsewhere."""
    return Path(os.getenv("AP_DB_PATH", str(DATA_DIR / "ap.db")))


# Which LLM to use: "azure" (Azure OpenAI) or "openai" (OpenAI platform)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure").strip().lower()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
# Leave empty to use the Azure OpenAI v1 endpoint. Only set it if your resource needs the older style.
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

# Hard guardrail: any exposure at or above this amount needs a human approver.
APPROVAL_THRESHOLD = float(os.getenv("APPROVAL_THRESHOLD", "50000"))
CURRENCY = os.getenv("CURRENCY", "INR")

# Used only for the impact estimate on the dashboard. It is an assumption, not a measurement.
MANUAL_MINUTES_PER_EXCEPTION = float(os.getenv("MANUAL_MINUTES_PER_EXCEPTION", "25"))

MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "25"))


def money(amount: float) -> str:
    return f"{CURRENCY} {amount:,.2f}"


def llm_config_problems() -> list[str]:
    """Return a list of missing settings. Empty list means the LLM is configured."""
    problems = []
    if LLM_PROVIDER == "azure":
        if not AZURE_OPENAI_ENDPOINT:
            problems.append("AZURE_OPENAI_ENDPOINT is empty")
        if not AZURE_OPENAI_API_KEY:
            problems.append("AZURE_OPENAI_API_KEY is empty")
        if not AZURE_OPENAI_DEPLOYMENT:
            problems.append("AZURE_OPENAI_DEPLOYMENT is empty")
    elif LLM_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            problems.append("OPENAI_API_KEY is empty")
    else:
        problems.append(f"LLM_PROVIDER must be 'azure' or 'openai', not '{LLM_PROVIDER}'")
    return problems

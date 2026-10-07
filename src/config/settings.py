import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
FIRECRAWL_ENABLED = os.getenv("FIRECRAWL_ENABLED", "true").lower() == "true"
try:
    FIRECRAWL_MAX_PER_RUN = int(os.getenv("FIRECRAWL_MAX_PER_RUN", "5"))
except ValueError:
    FIRECRAWL_MAX_PER_RUN = 5

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# PostgreSQL Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mu_hive")

# Optional orchestrator date window filters (UTC)
# Examples:
# ORCHESTRATOR_SINCE_DAYS=7
# ORCHESTRATOR_SINCE_DATE=2026-04-20
# ORCHESTRATOR_UNTIL_DATE=2026-04-28
ORCHESTRATOR_SINCE_DAYS = os.getenv("ORCHESTRATOR_SINCE_DAYS")
ORCHESTRATOR_SINCE_DATE = os.getenv("ORCHESTRATOR_SINCE_DATE")
ORCHESTRATOR_UNTIL_DATE = os.getenv("ORCHESTRATOR_UNTIL_DATE")


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- Phase 4B centralized rollout limits (conservative defaults) ---
# Maximum IGs processed per daily run (planner + notifiers).
MAX_IGS_PER_RUN = _int_env("MAX_IGS_PER_RUN", 30)
# Maximum discovery search queries per IG per category.
DISCOVERY_MAX_QUERIES_PER_IG = _int_env("DISCOVERY_MAX_QUERIES_PER_IG", 2)
# Maximum documents entering Intelligence per run.
INTELLIGENCE_MAX_DOCS_PER_RUN = _int_env("INTELLIGENCE_MAX_DOCS_PER_RUN", 30)
# Maximum notifications sent per IG per channel per run.
NOTIFY_PER_IG_LIMIT = _int_env("NOTIFY_PER_IG_LIMIT", 20)
# Registry-driven web search step (default off: preserves current workload).
SEARCH_ENABLED = os.getenv("SEARCH_ENABLED", "false").lower() == "true"
# Maximum results fetched per discovery search query.
SEARCH_MAX_RESULTS_PER_QUERY = _int_env("SEARCH_MAX_RESULTS_PER_QUERY", 3)

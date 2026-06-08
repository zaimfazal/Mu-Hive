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

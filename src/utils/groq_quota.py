# src/utils/groq_quota.py
import re
import logging

logger = logging.getLogger(__name__)

GROQ_TPD_LIMIT = 100_000      # Groq free tier actual TPD limit
GROQ_SAFETY_BUFFER = 15_000   # reserve — route to OpenRouter before hard 429s

_groq_tokens_used: int = 0    # module-level counter, updated from 429 bodies

def record_groq_usage(tokens_used: int):
    global _groq_tokens_used
    _groq_tokens_used = max(_groq_tokens_used, tokens_used)
    logger.info(f"Groq TPD updated: {_groq_tokens_used} used, {GROQ_TPD_LIMIT - _groq_tokens_used} remaining.")

def groq_quota_available() -> bool:
    remaining = GROQ_TPD_LIMIT - _groq_tokens_used
    return remaining > GROQ_SAFETY_BUFFER

def parse_and_record_groq_usage(error_message: str):
    # Format: "Used 499952, Requested 728"
    match = re.search(r"used\s+(\d+)", error_message, re.IGNORECASE)
    if match:
        try:
            used = int(match.group(1))
            record_groq_usage(used)
        except Exception as e:
            logger.error(f"Failed to parse tokens used from error: {e}")

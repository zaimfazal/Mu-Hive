import os
import sys
import logging
from dotenv import load_dotenv
from pydantic_ai.models.groq import GroqModel

load_dotenv()
logger = logging.getLogger(__name__)

from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.exceptions import ModelAPIError

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Fail fast if API key is missing — don't let the pipeline silently break
if not GROQ_API_KEY:
    logger.error(
        "GROQ_API_KEY is not set. "
        "Create a .env file with your key: GROQ_API_KEY=gsk_..."
    )
    sys.exit(1)

primary_model = GroqModel(model_name=GROQ_MODEL_NAME)
backup_model_name = "llama-3.1-8b-instant"

if GROQ_MODEL_NAME != backup_model_name:
    backup_model = GroqModel(model_name=backup_model_name)
    
    def log_and_fallback(exc: Exception) -> bool:
        if isinstance(exc, ModelAPIError):
            logger.warning(
                f"LLM API error on primary model '{GROQ_MODEL_NAME}': {exc}. "
                f"Automatically falling back to '{backup_model_name}'."
            )
            return True
        return False

    model = FallbackModel(primary_model, backup_model, fallback_on=[log_and_fallback])
    logger.info(f"LLM configured with fallback: primary={GROQ_MODEL_NAME}, backup={backup_model_name}")
else:
    model = primary_model
    logger.info(f"LLM configured: Groq/{GROQ_MODEL_NAME}")

import os

# Placeholder for LLM configuration
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))

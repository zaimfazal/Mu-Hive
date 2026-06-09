import os
import sys
import logging
from dotenv import load_dotenv
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.exceptions import ModelAPIError

load_dotenv()
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Fail fast if API key is missing — don't let the pipeline silently break
if not GROQ_API_KEY:
    logger.error(
        "GROQ_API_KEY is not set. "
        "Create a .env file with your key: GROQ_API_KEY=gsk_..."
    )
    sys.exit(1)

# ── Build model chain: Primary Groq → Backup Groq → OpenRouter fallback ──
primary_model = GroqModel(model_name=GROQ_MODEL_NAME)
backup_groq_name = "llama-3.1-8b-instant"

fallback_models = []

# Add Groq backup if primary is a different model
if GROQ_MODEL_NAME != backup_groq_name:
    fallback_models.append(GroqModel(model_name=backup_groq_name))

# Add OpenRouter as final fallback
if OPENROUTER_API_KEY:
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.settings import ModelSettings
    openrouter_provider = OpenAIProvider(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )
    openrouter_model = OpenAIModel(
        model_name="google/gemini-2.5-flash",
        provider=openrouter_provider,
        settings=ModelSettings(max_tokens=1000),
    )
    fallback_models.append(openrouter_model)
    logger.info("OpenRouter fallback enabled (google/gemini-2.5-flash) with token limit")

def _should_fallback(exc: Exception) -> bool:
    if isinstance(exc, ModelAPIError):
        logger.warning(f"LLM API error: {exc}. Falling back to next model...")
        return True
    return False

if fallback_models:
    model = FallbackModel(primary_model, *fallback_models, fallback_on=[_should_fallback])
    names = [GROQ_MODEL_NAME] + [backup_groq_name] * (GROQ_MODEL_NAME != backup_groq_name)
    if OPENROUTER_API_KEY:
        names.append("openrouter/gemini-flash-1.5")
    logger.info(f"LLM configured with fallback chain: {' → '.join(names)}")
else:
    model = primary_model
    logger.info(f"LLM configured: Groq/{GROQ_MODEL_NAME}")

# Placeholder for LLM configuration
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))

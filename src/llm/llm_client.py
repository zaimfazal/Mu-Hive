import os
import json
import re
import warnings
from typing import List, Dict, Any, Type, Optional
from loguru import logger
from openai import OpenAI
from aiolimiter import AsyncLimiter
from dotenv import load_dotenv
from src.config import load_config
from src.utils.circuit_breaker import mark_failure, is_cooled_down

warnings.warn(
    "llm_client.py is deprecated and will be removed in a future release. Use the unified Pydantic AI agent framework.",
    DeprecationWarning,
    stacklevel=2
)

# Load env variables
load_dotenv()

_limiter_cache: Dict[str, AsyncLimiter] = {}

def get_limiter(provider: str, model_name: str) -> AsyncLimiter:
    cfg = load_config("models")
    key = f"{provider}:{model_name}"
    if key not in _limiter_cache:
        rpm = cfg.get("providers", {}).get(provider, {}).get("rpm", 5)
        _limiter_cache[key] = AsyncLimiter(rpm, 60)
    return _limiter_cache[key]

class LLMClient:
    def __init__(self, client: OpenAI, model: str, temperature: float, max_tokens: int, provider: str):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.provider = provider

    def create(self, response_model: Type[Any], messages: List[Dict[str, str]]) -> Any:
        # Generate schema
        json_schema = json.dumps(response_model.model_json_schema(), indent=2)
        local_msgs = [m.copy() for m in messages]
        local_msgs[0]["content"] += f"\n\nYou MUST return a JSON object. Model your response on this schema, but return ONLY the data:\n{json_schema}"
        
        try:
            res = self.client.chat.completions.create(
                model=self.model, messages=local_msgs, 
                temperature=self.temperature, max_tokens=self.max_tokens
            )
            raw = res.choices[0].message.content or ""
            
            # Extract JSON block
            match = re.search(r'(\{.*\})', raw, re.DOTALL)
            if not match:
                # If no {} found, just try the raw string
                return response_model.model_validate_json(raw.strip())
            
            clean_json = match.group(1)
            
            # Pydantic Hack: Parse as dict first to allow extra fields the AI might add
            data = json.loads(clean_json)
            # Remove the common 'description' field if the AI echo-ed it
            if "description" in data and len(data) > 1:
                del data["description"]
                
            return response_model.model_validate(data)
            
        except Exception as e:
            if "429" in str(e): mark_failure(self.provider)
            logger.error(f"LLM Error during parsing: {e} | Raw: {raw[:100]}")
            raise e

def make_llm_client(role: str, tier: str = "cheap") -> Optional[LLMClient]:
    cfg = load_config("models")
    if not cfg or "roles" not in cfg: return None
    role_cfg = cfg["roles"].get(role)
    if not role_cfg: return None
    
    settings = role_cfg.get(tier) or list(role_cfg.values())[0]
    provider = settings["provider"]
    p_cfg = cfg.get("providers", {}).get(provider, {})
    
    if not is_cooled_down(provider): return make_llm_client(role, "fallback")
    
    api_key = os.environ.get(p_cfg.get("api_key_env", "GROQ_API_KEY"))
    if not api_key: return None
    
    return LLMClient(
        client=OpenAI(base_url=p_cfg.get("base_url"), api_key=api_key),
        model=settings["model"],
        temperature=float(settings.get("temperature", 0.05)),
        max_tokens=settings["max_tokens"],
        provider=provider
    )

import logging
import time
import re
from typing import List
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent
import asyncio

from src.db.postgres_database import DatabaseFacade as Database
from src.config.agent_config import model
from src.config.logging_config import setup_logging
from src.agents.planner import MVP_IGS
from src.agents.summarizer import summarizer_agent

logger = logging.getLogger(__name__)
MAX_LLM_API_ERRORS_BEFORE_FAIL = 5
HARD_FAIL_STATUS_CODES = {400, 429}

_VALID_IGS = set(MVP_IGS)
_IG_NORMALIZATION = {
    "ai": "AI",
    "data science": "Data Science",
    "web development": "Web Development",
    "cyber security": "Cyber Security",
    "cybersecurity": "Cyber Security",
    "ui/ux": "UI/UX",
    "ui ux": "UI/UX",
}


class OpportunityIntelligence(BaseModel):
    """Schema enforced by Pydantic AI for LLM structured output."""
    is_relevant: bool = Field(
        description="True if this is informative tech/AI news or a real opportunity. False if spam or irrelevant."
    )
    quality_score: int = Field(
        ge=1, le=10,
        description="Score from 1-10 assessing the novelty, quality, and importance of the article."
    )
    reasoning: str = Field(
        description="A short 1-sentence explanation of the score."
    )
    ig_tags: List[str] = Field(
        description=f"Select the most relevant Interest Groups from: {', '.join(MVP_IGS)}. Must select at least one if relevant."
    )

    @field_validator("ig_tags")
    @classmethod
    def validate_ig_tags(cls, tags):
        normalized_tags = []
        seen = set()
        for tag in tags or []:
            normalized = _normalize_ig(tag)
            if not normalized:
                raise ValueError(
                    f"Unsupported IG tag: {tag!r}. Allowed tags: {', '.join(MVP_IGS)}."
                )
            if normalized not in seen:
                normalized_tags.append(normalized)
                seen.add(normalized)
        return normalized_tags


class LLMFailureThresholdExceeded(RuntimeError):
    """Raised when too many hard LLM API failures occur in one run."""


INTELLIGENCE_SYSTEM_PROMPT = """\
You are an expert technical intelligence classifier for Mu-Hive, a tech community platform.
Your job is to evaluate scraped articles/opportunities and classify them into the correct Interest Groups (IGs).

## Interest Group Definitions (ONLY tag if content is DIRECTLY about these topics):

- **AI**: Artificial intelligence, machine learning, deep learning, LLMs, NLP, computer vision, neural networks, GenAI, AI research papers, AI tools and frameworks (TensorFlow, PyTorch, Hugging Face).
- **Data Science**: Data analytics, data engineering, big data, data visualization, statistical modeling, Kaggle, pandas, business intelligence, data pipelines.
- **Web Development**: Frontend/backend development, JavaScript, TypeScript, React, Next.js, Node.js, Django, Flask, Vue, Angular, HTML/CSS, web frameworks, APIs, DevOps, cloud deployment.
- **Cyber Security**: Cybersecurity, infosec, ethical hacking, penetration testing, CTFs, vulnerability disclosures, malware analysis, threat intelligence, SOC, network security, zero-day exploits, trojans, phishing, ransomware, NFC attacks, data breaches, APT campaigns.
- **UI/UX**: User experience design, user interface design, UX research, Figma, prototyping, wireframing, interaction design, product design, usability testing, design systems.

## Strict Classification Rules:
1. ONLY tag an IG if the content is DIRECTLY and PRIMARILY about that domain. Do NOT tag loosely related content.
2. Political news, sports, entertainment, world events, opinion pieces about non-tech topics, and general business news are NEVER relevant. Set is_relevant=False and quality_score=1 for these.
3. If the content mentions tech only tangentially (e.g., a political article that briefly mentions AI policy), it is NOT relevant.
4. Be strict with quality scores: 1-3 = low quality/irrelevant, 4-5 = borderline, 6-7 = good, 8-9 = very good, 10 = groundbreaking.
5. If content has no clear connection to ANY tech Interest Group, set is_relevant=False and quality_score=1.
6. A single article can belong to multiple IGs ONLY if it substantively covers multiple domains.

## Common Misclassification Errors — DO NOT make these mistakes:
- Malware, trojans, phishing, NFC attacks, data breaches, ransomware, APT groups → these are ONLY "Cyber Security", NEVER "AI"
- An article about a security vulnerability or hacking campaign is NOT "AI" just because it involves technology
- Hardware news, chip manufacturing, semiconductor news → NOT any IG unless it is specifically about AI chips/models
- General tech company earnings, mergers, layoffs → NOT relevant unless specifically about the IG's domain
- A cybersecurity tool that uses ML internally is still "Cyber Security", NOT "AI" — classify by the article's PRIMARY topic
- Design of physical products, architecture, fashion design → NOT "UI/UX" (UI/UX is digital interface design only)
"""

intelligence_agent = Agent(
    model,
    output_type=OpportunityIntelligence,
    system_prompt=INTELLIGENCE_SYSTEM_PROMPT,
)


def _normalize_ig(tag: str) -> str | None:
    if not tag:
        return None
    canonical = _IG_NORMALIZATION.get(str(tag).strip().lower())
    if canonical:
        return canonical
    stripped = str(tag).strip()
    if stripped in _VALID_IGS:
        return stripped
    return None


def _validate_tags(llm_tags: list, source_ig: list) -> list:
    """
    Ensure final tags stay in supported IGs and always include source_ig tags
    from the database.
    """
    validated = []
    seen = set()

    for tag in llm_tags or []:
        normalized = _normalize_ig(tag)
        if normalized and normalized not in seen:
            validated.append(normalized)
            seen.add(normalized)

    for tag in source_ig or []:
        normalized = _normalize_ig(tag)
        if normalized and normalized not in seen:
            logger.info("  -> Added source IG tag '%s' to model tags", normalized)
            validated.append(normalized)
            seen.add(normalized)

    return validated


def _extract_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    response = getattr(exc, "response", None)
    response_code = getattr(response, "status_code", None)
    if isinstance(response_code, int):
        return response_code

    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_status = getattr(cause, "status_code", None)
        if isinstance(cause_status, int):
            return cause_status
        cause_response = getattr(cause, "response", None)
        cause_response_code = getattr(cause_response, "status_code", None)
        if isinstance(cause_response_code, int):
            return cause_response_code

    match = re.search(r"\b(400|429)\b", str(exc))
    if match:
        return int(match.group(1))

    return None


async def run_agent_with_retry(agent, prompt, max_retries=3, initial_delay=5):
    """Runs a pydantic-ai agent with exponential backoff on 429 rate limit errors."""
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return await agent.run(prompt)
        except Exception as e:
            status_code = _extract_status_code(e)
            if status_code == 429 and attempt < max_retries:
                logger.warning(
                    f"LLM API returned 429 (Rate Limit). Retrying in {delay}s (Attempt {attempt+1}/{max_retries})..."
                )
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise e


async def run_intelligence(batch_limit=15):
    """
    Evaluates and classifies unprocessed opportunities using the LLM.
    Processes up to batch_limit items per run to conserve API calls.
    """
    logger.info("Initializing Intelligence Agent (Evaluator + Classifier)...")
    db = Database()
    hard_fail_count = 0

    try:
        docs = db.get_unprocessed_for_intelligence(limit=batch_limit)

        if not docs:
            logger.info("No new opportunities require evaluation.")
            return

        processed_count = 0
        for doc in docs:
            item_id = doc["_id"]
            title = doc.get("title", "")
            # Use full extracted text if available; otherwise fallback to summary
            content = doc.get("scraped_full_text") or doc.get("summary", "")
            category = doc.get("category", "Unknown")
            source_ig = doc.get("ig_tags", [])

            # Include source context so LLM can validate/reject initial classification
            prompt = (
                f"Title: {title}\n"
                f"Category: {category}\n"
                f"Original IG Tags (from source — validate or override these): {source_ig}\n"
                f"Content: {content[:3000]}\n\n"
                "Evaluate this content's relevance and quality. "
                "Classify into the correct Interest Groups."
            )
            logger.info(f"Evaluating ID {item_id}: {title[:60]}...")

            try:
                result = await run_agent_with_retry(intelligence_agent, prompt)
                intelligence: OpportunityIntelligence = result.output

                final_score = intelligence.quality_score if intelligence.is_relevant else 0

                # Post-LLM validation to catch misclassifications
                validated_tags = _validate_tags(intelligence.ig_tags, source_ig)

                # Generate LLM summary for high-quality items (Bug 7 fix)
                generated_summary = None
                if final_score >= 6 and category in ("News", "Unknown"):
                    try:
                        summary_prompt = (
                            f"Title: {title}\n"
                            f"Interest Groups: {', '.join(validated_tags)}\n"
                            f"Content: {content[:1500]}\n\n"
                            "Write a crisp 1-sentence summary."
                        )
                        summary_result = await run_agent_with_retry(summarizer_agent, summary_prompt)
                        generated_summary = summary_result.output.summary
                        logger.info(f"  -> Summary: {generated_summary[:80]}...")
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning(f"  -> Summarizer failed for ID {item_id}: {e}")

                db.update_intelligence(item_id, final_score, validated_tags, generated_summary=generated_summary)
                processed_count += 1
                logger.info(
                    f"  -> Score: {final_score} | Tags: {validated_tags} | {intelligence.reasoning}"
                )

                await asyncio.sleep(3)
            except Exception as e:
                status_code = _extract_status_code(e)
                if status_code in HARD_FAIL_STATUS_CODES:
                    hard_fail_count += 1
                    logger.error(
                        "Hard LLM API error for ID %s (HTTP %s). Count=%s/%s.",
                        item_id,
                        status_code,
                        hard_fail_count,
                        MAX_LLM_API_ERRORS_BEFORE_FAIL,
                    )
                    if hard_fail_count > MAX_LLM_API_ERRORS_BEFORE_FAIL:
                        raise LLMFailureThresholdExceeded(
                            "LLM API failure threshold exceeded: "
                            f"{hard_fail_count} HTTP 400/429 errors in one run."
                        ) from e

                logger.error(f"Intelligence processing failed for ID {item_id}: {e}")
                db.update_intelligence(item_id, 0, [])

        logger.info(
            f"Intelligence Agent finished. Evaluated {processed_count}/{len(docs)} items."
        )
    finally:
        db.close()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_intelligence())

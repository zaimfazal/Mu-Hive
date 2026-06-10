import logging
import time
import re
from typing import List
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent
import asyncio

from src.db.postgres_database import DatabaseFacade as Database
from src.config.agent_config import model, shared_model_settings
from src.config.logging_config import setup_logging
from src.config.sources import SOURCE_PRIORITY
from src.config.constants import MASTER_IGS

logger = logging.getLogger(__name__)
MAX_LLM_API_ERRORS_BEFORE_FAIL = 5
HARD_FAIL_STATUS_CODES = {400, 429}

_VALID_IGS = set(MASTER_IGS)
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
        description=f"Select the most relevant Interest Groups from: {', '.join(MASTER_IGS)}. Must select at least one if relevant."
    )
    category: str = Field(
        description="Must be exactly 'News' or 'Hackathons'. Use 'Hackathons' ONLY for actual upcoming hackathon/competition listings with registration links. Everything else (articles, tutorials, announcements, opinion pieces) is 'News'."
    )
    structured_metadata: dict | None = Field(
        default=None,
        description="ONLY when category='Hackathons': extract available fields as {'Platform': '', 'Start': '', 'End': '', 'Location': '', 'Mode': 'Online/Offline/Hybrid', 'Prize Pool': '', 'Registration Link': '', 'Cost': '', 'Eligibility': ''}. For News, return null."
    )
    summary: str | None = Field(
        default=None,
        description="Write a crisp 1-sentence summary of the article if it is relevant. Return null if is_relevant is False."
    )

    @field_validator("ig_tags")
    @classmethod
    def validate_ig_tags(cls, tags):
        normalized_tags = []
        seen = set()
        for tag in tags or []:
            normalized = _normalize_ig(tag)
            if normalized and normalized not in seen:
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

## Category Rules (STRICT — only 2 categories exist):
- **Hackathons**: ONLY for actual upcoming hackathon/competition listings that people can register for. Must have a registration link or signup page. An article *about* a hackathon or reporting on hackathon results is NOT a hackathon — it is News.
- **News**: Everything else — articles, tutorials, announcements, opinion pieces, research papers, product launches, blog posts, reports, etc.

## Trending & Hot-Topic Scoring (AI moves fast — prioritize what matters NOW):
Score 9-10 (GROUNDBREAKING / MUST-READ):
- New frontier model releases (GPT-5, Gemini 3, Claude 4, Llama 4, etc.)
- Major open-source model drops (new SOTA on benchmarks)
- Critical zero-day vulnerabilities or massive data breaches
- Paradigm shifts: new architectures, novel training methods, agentic AI breakthroughs
- First-party announcements from OpenAI, Google DeepMind, Anthropic, Meta AI, xAI, Mistral

Score 7-8 (HIGH VALUE / TRENDING):
- Significant product launches, API releases, framework updates (e.g., new React version, major library release)
- Trending community discussions (viral posts, controversial takes with substance)
- Important research papers with real-world implications
- Major funding rounds, acquisitions, or strategic partnerships in AI/tech
- New developer tools or platforms that change workflows

Score 5-6 (USEFUL / INFORMATIVE):
- Solid tutorials on cutting-edge topics (RAG, fine-tuning, agents)
- Industry analysis and trend reports with original data
- Conference talk summaries with novel insights
- Security advisories and patch announcements

Score 1-4 (LOW PRIORITY):
- Rehashed or rewritten content from other sources
- Generic listicles, opinion pieces without new information
- Minor patch notes, incremental updates
- Promotional content or thinly veiled advertisements
- Old news or outdated content

## Strict Classification Rules:
1. ONLY tag an IG if the content is DIRECTLY and PRIMARILY about that domain. Do NOT tag loosely related content.
2. Political news, sports, entertainment, world events, opinion pieces about non-tech topics, and general business news are NEVER relevant. Set is_relevant=False and quality_score=1 for these.
3. If the content mentions tech only tangentially (e.g., a political article that briefly mentions AI policy), it is NOT relevant.
4. If content has no clear connection to ANY tech Interest Group, set is_relevant=False and quality_score=1.
5. A single article can belong to multiple IGs ONLY if it substantively covers multiple domains.

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
    model_settings=shared_model_settings,
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
    Ensure final tags stay in supported IGs.
    If the LLM returns any valid tags, use them exclusively.
    Only use source_ig tags as a fallback if the LLM returns no valid tags.
    """
    validated = []
    seen = set()

    for tag in llm_tags or []:
        normalized = _normalize_ig(tag)
        if normalized and normalized not in seen:
            validated.append(normalized)
            seen.add(normalized)

    # Only fall back to source_ig if the LLM returned nothing valid
    if not validated:
        for tag in source_ig or []:
            normalized = _normalize_ig(tag)
            if normalized and normalized not in seen:
                logger.info("  -> Added source IG tag '%s' to model tags as fallback", normalized)
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
            content = doc.get("scraped_full_text") or doc.get("summary") or ""
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

                raw_score = intelligence.quality_score if intelligence.is_relevant else 0

                # ── Source boost: RSS +2, API +1, Search +0 ──
                source_engine = doc.get("source", "")
                source_boost = SOURCE_PRIORITY.get(source_engine, 0)

                # ── Recency bonus: +1 for articles published < 24 hours ago ──
                recency_bonus = 0
                json_data = doc.get("data") or {}
                published_at = json_data.get("published_at")
                if published_at:
                    try:
                        hours_ago = (time.time() - float(published_at)) / 3600
                        if hours_ago < 24:
                            recency_bonus = 1
                    except (ValueError, TypeError):
                        pass

                # Restructured scoring logic (only apply bonus if raw_score >= 6)
                bonus = source_boost + recency_bonus
                if raw_score == 0:
                    final_score = 0
                    score_breakdown = "0 (irrelevant)"
                elif raw_score >= 6:
                    final_score = min(10, raw_score + bonus)
                    score_breakdown = f"{raw_score} + {bonus} bonus = {final_score}"
                else:
                    final_score = raw_score
                    score_breakdown = f"{raw_score} (no bonus applied as raw score < 6)"

                # Post-LLM validation to catch misclassifications
                validated_tags = _validate_tags(intelligence.ig_tags, source_ig)
                
                from src.utils.ig_normalizer import normalize_ig
                normalized_tags = []
                for tag in validated_tags:
                    canonical = normalize_ig(tag)
                    if canonical:
                        normalized_tags.append(canonical)
                
                validated_tags = normalized_tags
                ig = validated_tags[0] if validated_tags else None
                
                if ig is None or ig.strip().lower() == "unknown":
                    logger.warning(f"Skipping item {item_id}: invalid IG '{ig}'")
                    db.update_intelligence(item_id, 0, [])
                    processed_count += 1
                    continue
                
                final_category = intelligence.category

                generated_summary = intelligence.summary if final_score >= 6 else None
                if generated_summary:
                    logger.info(f"  -> Summary: {generated_summary[:80]}...")

                db.update_intelligence(
                    item_id, 
                    final_score, 
                    validated_tags, 
                    generated_summary=generated_summary, 
                    category=final_category,
                    raw_score=raw_score,
                    score_breakdown=score_breakdown,
                    structured_metadata=intelligence.structured_metadata
                )

                # Sync the Groq-generated summary to the events table
                if generated_summary:
                    link = doc.get("url", "")
                    if link:
                        db.update_event_summary_by_link(link, generated_summary)
                processed_count += 1
                logger.info(
                    f"  -> Score: {score_breakdown} | "
                    f"Tags: {validated_tags} | Category: {final_category} | {intelligence.reasoning}"
                )

                await asyncio.sleep(2.5)
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

                logger.error(f"Intelligence processing failed for ID {item_id}: {e}", exc_info=True)
                try:
                    db.update_intelligence(item_id, 0, [])
                except Exception as update_err:
                    logger.error(f"Failed to mark item {item_id} as failed in DB: {update_err}")

        logger.info(
            f"Intelligence Agent finished. Evaluated {processed_count}/{len(docs)} items."
        )
    finally:
        db.close()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_intelligence())

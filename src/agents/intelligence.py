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
MAX_LLM_API_ERRORS_BEFORE_FAIL = 10
HARD_FAIL_STATUS_CODES = {400, 429}

_VALID_IGS = set(MASTER_IGS)

from src.utils.ig_normalizer import normalize_ig as _canonical_ig


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
You are a technical intelligence classifier for Mu-Hive, a tech community platform.
Evaluate articles and classify them into Interest Groups (IGs).

## Interest Groups (tag ONLY if content is DIRECTLY about these):
- **AI**: ML, deep learning, LLMs, NLP, computer vision, GenAI, AI frameworks.
- **Data Science**: Analytics, data engineering, visualization, statistical modeling, data pipelines.
- **Web Development**: Frontend/backend dev, JS/TS, React, Node.js, Django, Flask, APIs, DevOps, cloud.
- **Cyber Security**: Infosec, ethical hacking, CTFs, vulnerabilities, malware, threat intel, data breaches, ransomware, phishing.
- **UI/UX**: UX/UI design, UX research, Figma, prototyping, interaction design, design systems.

## Categories (STRICT — only 2):
- **Hackathons**: ONLY actual upcoming hackathon/competition listings with registration links. Articles *about* hackathons = News.
- **News**: Everything else.

## Scoring Guide:
- 9-10: Frontier model releases, major SOTA drops, critical zero-days, paradigm shifts, first-party announcements (OpenAI, DeepMind, Anthropic, Meta AI).
- 7-8: Significant launches/updates, trending discussions, impactful research, major funding/acquisitions.
- 5-6: Solid tutorials, industry analysis, conference insights, security advisories.
- 1-4: Rehashed content, listicles, minor updates, promotional, outdated.

## Rules:
1. Tag IGs only if content is DIRECTLY about that domain.
2. Non-tech content (politics, sports, entertainment) → is_relevant=False, quality_score=1.
3. Tangential tech mentions → NOT relevant.
4. Multiple IGs only if article substantively covers multiple domains.
5. Malware/trojans/phishing/breaches → "Cyber Security" only, NEVER "AI".
6. Cybersecurity tools using ML internally → classify by PRIMARY topic ("Cyber Security").
7. Hardware/semiconductor news → NOT any IG unless specifically about AI models.
8. Physical product design → NOT "UI/UX" (digital interface design only).

Return the pure JSON structured object directly. Do NOT wrap in XML tags.
"""

intelligence_agent = Agent(
    model,
    output_type=OpportunityIntelligence,
    model_settings=shared_model_settings,
    system_prompt=INTELLIGENCE_SYSTEM_PROMPT,
    retries=3,
)


def _normalize_ig(tag: str) -> str | None:
    """Normalize an IG tag using the canonical ig_normalizer as single source of truth."""
    if not tag:
        return None
    # First try the canonical normalizer (covers all known variants)
    canonical = _canonical_ig(str(tag).strip())
    if canonical:
        return canonical
    # Fallback: exact match against MASTER_IGS (handles already-canonical names)
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


def _normalize_category(category: str | None) -> str:
    value = str(category or "").strip().lower()
    if value == "hackathons":
        return "Hackathons"
    return "News"


def _compute_trend_bonus(title: str, content: str, category: str) -> int:
    """Small deterministic tie-breaker for genuinely hot news signals."""
    if category != "News":
        return 0

    blob = f"{title} {content[:500]}".lower()
    hot_terms = [
        "launch", "released", "announces", "open source", "sota", "state-of-the-art",
        "benchmark", "frontier", "zero-day", "0-day", "cve-", "breach",
        "ransomware", "acquisition", "funding", "research paper", "model weights",
    ]
    return 1 if any(term in blob for term in hot_terms) else 0


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


async def run_agent_with_retry(agent, prompt, max_retries=5, initial_delay=5):
    """Runs a pydantic-ai agent with exponential backoff on 429 rate limit errors.
    Parses the retry-after hint from the error body when available.
    Tuned for Groq free tier: more retries, longer wait caps."""
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return await agent.run(prompt)
        except Exception as e:
            status_code = _extract_status_code(e)
            if status_code == 429 and attempt < max_retries:
                # Try to parse the retry-after hint from the error body
                parsed_delay = _parse_retry_after(str(e))
                if parsed_delay and parsed_delay > 0:
                    wait_time = min(parsed_delay, 180)  # Cap at 3 minutes for free tier
                else:
                    wait_time = delay
                logger.warning(
                    f"LLM API returned 429 (Rate Limit). Retrying in {wait_time:.0f}s (Attempt {attempt+1}/{max_retries})..."
                )
                await asyncio.sleep(wait_time)
                delay *= 2
            else:
                raise e


def _parse_retry_after(error_text: str) -> float | None:
    """Parse the retry-after duration from Groq's 429 error message.
    Example: 'Please try again in 8m35.1168s'"""
    match = re.search(r'try again in\s+(?:(\d+)m)?([\d.]+)s', error_text, re.IGNORECASE)
    if match:
        minutes = int(match.group(1)) if match.group(1) else 0
        seconds = float(match.group(2))
        return minutes * 60 + seconds
    return None


async def run_intelligence(batch_limit=15):
    """
    Evaluates and classifies unprocessed opportunities using the LLM.
    Processes up to batch_limit items per run to conserve API calls.
    Returns a stats dict for phase reporting.
    """
    logger.info("Initializing Intelligence Agent (Evaluator + Classifier)...")
    db = Database()
    hard_fail_count = 0

    stats = {
        "total_evaluated": 0,
        "processed": 0,
        "irrelevant": 0,
        "skipped_invalid_ig": 0,
        "by_ig": {},
        "by_category": {},
        "by_score": {"9-10": 0, "7-8": 0, "5-6": 0, "1-4": 0, "0": 0},
        "llm_errors": 0,
        "llm_retries": 0,
    }

    try:
        docs = db.get_unprocessed_for_intelligence(limit=batch_limit)

        if not docs:
            logger.info("No new opportunities require evaluation.")
            return stats

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
                f"Content: {content[:1500]}\n\n"
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

                trend_bonus = _compute_trend_bonus(title, content, _normalize_category(intelligence.category))

                # Restructured scoring logic (only apply a capped bonus if raw_score >= 6).
                bonus = min(2, source_boost + recency_bonus + trend_bonus)
                if raw_score == 0:
                    final_score = 0
                    score_breakdown = "0 (irrelevant)"
                elif raw_score >= 6:
                    final_score = min(10, raw_score + bonus)
                    score_breakdown = (
                        f"{raw_score} + {bonus} bonus "
                        f"(source={source_boost}, recency={recency_bonus}, trend={trend_bonus}) = {final_score}"
                    )
                else:
                    final_score = raw_score
                    score_breakdown = f"{raw_score} (no bonus applied as raw score < 6)"

                # Post-LLM validation — single normalization pass using ig_normalizer
                validated_tags = _validate_tags(intelligence.ig_tags, source_ig)

                ig = validated_tags[0] if validated_tags else None
                
                if ig is None or ig.strip().lower() == "unknown":
                    logger.warning(f"Skipping item {item_id}: invalid IG '{ig}'")
                    db.update_intelligence(item_id, 0, [])
                    stats["skipped_invalid_ig"] += 1
                    processed_count += 1
                    continue
                
                final_category = _normalize_category(intelligence.category)

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

                # ── Track stats ──
                stats["total_evaluated"] += 1
                if final_score > 0:
                    stats["processed"] += 1
                else:
                    stats["irrelevant"] += 1

                # Score bucket
                if final_score == 0:
                    stats["by_score"]["0"] += 1
                elif final_score <= 4:
                    stats["by_score"]["1-4"] += 1
                elif final_score <= 6:
                    stats["by_score"]["5-6"] += 1
                elif final_score <= 8:
                    stats["by_score"]["7-8"] += 1
                else:
                    stats["by_score"]["9-10"] += 1

                # IG distribution
                for tag in validated_tags:
                    stats["by_ig"][tag] = stats["by_ig"].get(tag, 0) + 1

                # Category distribution
                stats["by_category"][final_category] = stats["by_category"].get(final_category, 0) + 1

                processed_count += 1
                logger.info(
                    f"  -> Score: {score_breakdown} | "
                    f"Tags: {validated_tags} | Category: {final_category} | {intelligence.reasoning}"
                )

                await asyncio.sleep(4)  # Pacing for Groq free tier (30 RPM)
            except Exception as e:
                status_code = _extract_status_code(e)
                if status_code in HARD_FAIL_STATUS_CODES:
                    hard_fail_count += 1
                    stats["llm_errors"] += 1
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

                stats["llm_errors"] += 1
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

    return stats


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_intelligence())

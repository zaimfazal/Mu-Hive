import logging
import asyncio
from src.scraping.scraper import run_scraper_pipeline 
from src.scraping.scraper_agent import run_rss_agent, run_scraper_agent
from src.agents.intelligence import run_intelligence, LLMFailureThresholdExceeded
from src.agents.communicator import run_communicator
from src.db.orchestrator_writer import save_orchestrator_events
from src.config.logging_config import setup_logging

logger = logging.getLogger(__name__)

# --- Pipeline Configuration ---
INTELLIGENCE_BATCH_LIMIT = 30


class Orchestrator:
    """Compatibility wrapper for one-off URL processing scripts."""

    @staticmethod
    def _sanitize_url(url):
        if not url:
            return url
        url = url.strip()
        if url.startswith("https://https://"):
            return url.replace("https://https://", "https://", 1)
        if url.startswith("http://http://"):
            return url.replace("http://http://", "http://", 1)
        return url

    async def process_url(self, url, mode="scrape", options=None):
        from src.db.postgres_database import DatabaseFacade
        from src.scraping.scraper_agent import scrape_url

        clean_url = self._sanitize_url(url)
        result = await scrape_url(clean_url)
        if not result:
            return {"url": clean_url, "error": "No content extracted"}

        db = DatabaseFacade()
        try:
            data = {
                "scraped_full_text": result["text"],
                "scraped_page_title": result["page_title"],
                "scraped_meta_description": result["meta_description"],
            }
            record_id = db.save_scrape_result(
                clean_url,
                data,
                title=result["page_title"] or clean_url,
                status="scraped",
            )
        finally:
            db.close()

        return {
            "url": clean_url,
            "record_id": record_id,
            "scrape_layer": result["layer"],
        }

    async def run_batch(self, urls, mode="scrape", options=None):
        tasks = [self.process_url(url, mode=mode, options=options) for url in urls]
        return await asyncio.gather(*tasks)


orchestrator = Orchestrator()


def _log_phase_report(phase_name: str, stats: dict | None):
    """Logs a structured phase report block."""
    if not stats:
        logger.info(f"  (no stats returned)")
        return

    separator = "─" * 50
    logger.info(separator)
    for key, value in stats.items():
        if isinstance(value, dict):
            # Format nested dicts inline
            formatted = " | ".join(f"{k}={v}" for k, v in value.items() if v)
            if formatted:
                label = key.replace("_", " ").title()
                logger.info(f"  {label:.<30s} {formatted}")
        else:
            label = key.replace("_", " ").title()
            logger.info(f"  {label:.<30s} {value}")
    logger.info(separator)


async def run_pipeline():
    """Executes the full Mu-Hive intelligence pipeline sequentially.
    Each phase is error-isolated so failures don't block subsequent phases."""
    logger.info("=" * 50)
    logger.info("Mu-Hive Intelligence Pipeline - Starting")
    logger.info("=" * 50)

    # Phase 1: Scout — Search & Scraping
    rss_stats = None
    scraper_stats = None
    try:
        logger.info("Phase 1: Running Scout Agent (Search & Scraping)...")

        # Phase 1a: RSS feeds
        rss_stats = await run_rss_agent()

        # Phase 1b: Hackathon API scraping
        grouped_events = await run_scraper_pipeline()
        save_result = await save_orchestrator_events(grouped_events)
        logger.info(
            "Saved orchestrator output to DB: %s scraped entries, %s event rows.",
            save_result["inserted_scraped"],
            save_result["upserted_events"],
        )
        
        # Phase 1c: Scrape details for newly added items
        logger.info("Running Scraper Agent to process pending event links...")
        scraper_stats = await run_scraper_agent()
    except Exception as e:
        logger.error(f"Scout Agent failed: {e}. Continuing with existing data...")

    # Phase 1 Report
    logger.info("")
    logger.info("=" * 50)
    logger.info("  📊 PHASE 1 REPORT — Scout Agent (Search & Scraping)")
    logger.info("=" * 50)
    if rss_stats:
        logger.info("  [RSS Agent]")
        _log_phase_report("RSS", rss_stats)
    if scraper_stats:
        logger.info("  [Scraper Agent]")
        _log_phase_report("Scraper", scraper_stats)
    logger.info("")

    # Phase 2: Intelligence — LLM evaluation
    intel_stats = None
    try:
        logger.info("Phase 2: Running Intelligence Agent (LLM evaluation)...")
        intel_stats = await run_intelligence(batch_limit=INTELLIGENCE_BATCH_LIMIT)
    except LLMFailureThresholdExceeded as e:
        logger.error(f"Intelligence Agent hard-failed: {e}. Continuing to Phase 3 with items scored so far...")
    except Exception as e:
        logger.error(f"Intelligence Agent failed: {e}. Continuing with existing scores...")

    # Phase 2 Report
    logger.info("")
    logger.info("=" * 50)
    logger.info("  📊 PHASE 2 REPORT — Intelligence Agent (LLM Evaluation)")
    logger.info("=" * 50)
    _log_phase_report("Intelligence", intel_stats)
    logger.info("")

    # Phase 3: Communicator — curated digest output
    # NOTE: Terminal displays the curated top-N items per IG (limit=5 per category),
    # while Zulip/Email agents send ALL qualifying items (score >= 6) to their
    # respective channels. This is by design — terminal is a curated preview,
    # distribution channels get the full qualified set.
    comm_stats = None
    try:
        logger.info("Phase 3: Running Communicator (curated digest output)...")
        comm_stats = run_communicator()
    except Exception as e:
        logger.error(f"Communicator failed: {e}")

    # Phase 3 Report
    logger.info("")
    logger.info("=" * 50)
    logger.info("  📊 PHASE 3 REPORT — Communicator (Curated Digest)")
    logger.info("=" * 50)
    _log_phase_report("Communicator", comm_stats)
    logger.info("")

    logger.info("Pipeline execution complete.")

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_pipeline())

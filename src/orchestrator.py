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
INTELLIGENCE_BATCH_LIMIT = 500

async def run_pipeline():
    """Executes the full Mu-Hive intelligence pipeline sequentially.
    Each phase is error-isolated so failures don't block subsequent phases."""
    logger.info("=" * 50)
    logger.info("Mu-Hive Intelligence Pipeline - Starting")
    logger.info("=" * 50)

    # Phase 1: Scout — Search & Scraping
    try:
        logger.info("Phase 1: Running Scout Agent (Search & Scraping)...")
        await run_rss_agent()
        grouped_events = await run_scraper_pipeline()
        save_result = await save_orchestrator_events(grouped_events)
        logger.info(
            "Saved orchestrator output to DB: %s scraped entries, %s event rows.",
            save_result["inserted_scraped"],
            save_result["upserted_events"],
        )
        
        # Scrape details for newly added hackathons (which have status 'not processed')
        logger.info("Running Scraper Agent to process pending event links...")
        await run_scraper_agent()
    except Exception as e:
        logger.error(f"Scout Agent failed: {e}. Continuing with existing data...")

    # Phase 2: Intelligence — LLM evaluation
    try:
        logger.info("Phase 2: Running Intelligence Agent (LLM evaluation)...")
        await run_intelligence(batch_limit=INTELLIGENCE_BATCH_LIMIT)
    except LLMFailureThresholdExceeded as e:
        logger.error(f"Intelligence Agent hard-failed: {e}. Aborting pipeline.")
        raise
    except Exception as e:
        logger.error(f"Intelligence Agent failed: {e}. Continuing with existing scores...")

    # Phase 3: Communicator — curated digest output
    try:
        logger.info("Phase 3: Running Communicator (curated digest output)...")
        run_communicator()
    except Exception as e:
        logger.error(f"Communicator failed: {e}")

    logger.info("Pipeline execution complete.")

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_pipeline())

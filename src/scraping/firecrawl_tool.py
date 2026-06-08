import os
import logging
from firecrawl import Firecrawl
from src.config.settings import FIRECRAWL_API_KEY

logger = logging.getLogger(__name__)

# Run-level call counter to keep track of Firecrawl API usage
_calls_made = 0

async def firecrawl_extract(url: str):
    """
    Scrapes a URL using the Firecrawl API.
    Returns (markdown, title, description, images) if successful, or (None, None, None, None).
    """
    global _calls_made
    
    # Import settings dynamically to allow for runtime changes
    from src.config.settings import FIRECRAWL_ENABLED, FIRECRAWL_MAX_PER_RUN
    
    if not FIRECRAWL_ENABLED:
        logger.info("Firecrawl: Skipped (disabled in settings)")
        return None, None, None, None
        
    if not FIRECRAWL_API_KEY:
        logger.warning("Firecrawl: Missing API key")
        return None, None, None, None
        
    if _calls_made >= FIRECRAWL_MAX_PER_RUN:
        logger.warning("Firecrawl: Skipped (reached max budget of %d per run)", FIRECRAWL_MAX_PER_RUN)
        return None, None, None, None
        
    _calls_made += 1
    logger.info("Firecrawl: Scraping page (call %d/%d) - %s", _calls_made, FIRECRAWL_MAX_PER_RUN, url)
    
    try:
        # Firecrawl v2 uses the Firecrawl class
        app = Firecrawl(api_key=FIRECRAWL_API_KEY)
        
        # Call scrape_url with format set to markdown
        response = app.scrape_url(url, params={"formats": ["markdown"]})
        
        if not response:
            return None, None, None, None
            
        # Extract fields from the response
        metadata = response.get("metadata", {})
        markdown = response.get("markdown", "")
        title = metadata.get("title", "")
        description = metadata.get("description", "")
        
        # Extract top image if available
        og_image = metadata.get("ogImage", "")
        images = [og_image] if og_image else []
        
        return markdown, title, description, images
    except Exception as e:
        logger.error("Firecrawl: Scrape failed for %s: %s", url, e)
        return None, None, None, None

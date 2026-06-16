import logging
logger = logging.getLogger(__name__)

import asyncio, base64, os, time
import httpx, trafilatura
import feedparser
from bs4 import BeautifulSoup
from readability import Document
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from src.db.postgres_database import DatabaseFacade
from src.utils.ig_normalizer import normalize_ig
from dotenv import load_dotenv

load_dotenv()
 
try:
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module='newspaper')
    from newspaper import Article as NArticle
    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False
 
# ── Config ────────────────────────────────────────────────────────────────────
MIN_WORDS    = 120
SCRAPE_LIMIT = 50
TIMEOUT      = 30   # seconds
PW_WAIT_MS   = 4000    # ms to wait after page load for JS
MAX_IMAGES   = 5       # max images extracted (URLs only)
USER_AGENT   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
feedparser.USER_AGENT = USER_AGENT
 
 
# ── Text extraction ───────────────────────────────────────────────────────────
# Using trafilatura, readability and newspaper 

def extract(html: str) -> str:
    """Run all extractors on HTML, return the longest result."""
    candidates = []
 
    t = trafilatura.extract(html, include_tables=True, favor_recall=True)
    candidates.append(t or "")
 
    try:
        soup = BeautifulSoup(Document(html).summary(), "lxml")
        candidates.append(soup.get_text(" ", strip=True))
    except Exception:
        pass
 
    if HAS_NEWSPAPER:
        try:
            art = NArticle(""); art.set_html(html); art.parse()
            candidates.append(art.text or "")
        except Exception:
            pass
 
    return max(candidates, key=lambda t: len(t.split()), default="")
 
 
# ── Layer 1: plain HTTP ───────────────────────────────────────────────────────
async def l1_httpx(url: str) -> tuple | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT,
                                     headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(url)
            r.raise_for_status()
 
        soup     = BeautifulSoup(r.text, "lxml")
        img_urls = [i["src"] for i in soup.find_all("img", src=True)
                    if i["src"].startswith("http")][:MAX_IMAGES]
        text     = extract(r.text)
 
        return (text, "", "", img_urls) if len(text.split()) >= MIN_WORDS else None
    except Exception as e:
        logger.info(f"    [L1:{url[:50]}] {e}"); return None
 
 
# ── Layer 2: Playwright (JS-rendered pages) ───────────────────────────────────
async def l2_playwright(url: str, browser) -> tuple | None:
    try:
        page = await (await browser.new_context(user_agent=USER_AGENT)).new_page()
        await Stealth().apply_stealth_async(page)  # one line, fixes most 403s
        
        await page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
        await page.wait_for_timeout(PW_WAIT_MS)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

        try:
            html = await page.content()
        except Exception:
            await page.close(); return None
        
        try:
            title = await page.title()
        except Exception:
            title = ""
            
        try:
            meta = await page.evaluate(
                'document.querySelector("meta[name=\'description\']")?.content || ""')
        except Exception:
            meta = ""
            
        try:
            imgs = await page.evaluate(
                "Array.from(document.images).map(i=>i.src).filter(s=>s.startsWith('http'))")
        except Exception:
            imgs = []
            
        await page.close()

        text = extract(html)
        return (text, title, meta, imgs[:MAX_IMAGES]) if len(text.split()) >= MIN_WORDS else None
    except Exception as e:
        logger.info(f"    [L2:{url[:50]}] {e}"); return None
 
 
from src.scraping.firecrawl_tool import firecrawl_extract

# ── Core scraping logic with structured output ────────────────────────────────────────────────────
async def scrape_url(url: str, browser=None) -> dict | None:
    """
    Scrape a URL, returning full text.
    Returns dict with keys: text, page_title, meta_description, layer
    or None if all layers fail.
    """
    logger.info(f"  [L1:{url[:50]}] trying...")
    result = await l1_httpx(url)
    label = "L1"
    
    if not result:
        logger.info(f"  [Firecrawl:{url[:50]}] trying...")
        fc_text, fc_title, fc_desc, fc_imgs = await firecrawl_extract(url)
        if fc_text and len(fc_text.split()) >= MIN_WORDS:
            result = (fc_text, fc_title, fc_desc, fc_imgs[:MAX_IMAGES])
            label = "Firecrawl"
            
    if not result:
        logger.info(f"  [L2:{url[:50]}] trying...")
        if browser:
            result = await l2_playwright(url, browser)
        else:
            async with async_playwright() as p:
                temp_browser = await p.chromium.launch(headless=True)
                result = await l2_playwright(url, temp_browser)
                await temp_browser.close()
        label = "L2"

    if result:
        text, title, meta, imgs = result
        logger.info(f"  [{label}:{url[:50]}] ✓ {len(text.split())} words")
        return {"text": text, "page_title": title, "meta_description": meta, "layer": label}

    logger.info(f"  [FAIL:{url[:50]}] all layers failed")
    return None
 
 
# ── Pending-links scraper ─────────────────────────────────────────────────────
#scraping links with status not processed 
async def run_scraper_agent(limit: int = SCRAPE_LIMIT):
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] scraper starting...")
    db      = DatabaseFacade()
    pending = db.find_pending_scrape(limit=limit)

    stats = {
        "pending": len(pending) if pending else 0,
        "scraped_ok": 0,
        "scrape_failed": 0,
        "spam_filtered": 0,
        "scrape_layers": {"L1": 0, "Firecrawl": 0, "L2": 0},
    }

    if not pending:
        logger.info("nothing to scrape.")
        db.close()
        return stats
    
    logger.info(f"{len(pending)} doc(s) queued.")
    semaphore = asyncio.Semaphore(5)

    async def scrape_one(doc, browser):
        async with semaphore:
            doc_id, link = doc["_id"], doc.get("link")
            logger.info(f"\n── {link}")

            # Track retry count from JSONB data field
            current_data = doc.get("data") or {}
            retry_count = current_data.get("retry_count", 0)

            if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
                current_data["retry_count"] = retry_count + 1
                db.update_event_scrape(doc_id, status="scrape_failed",
                                       scrape_error="invalid URL",
                                       retry_count=current_data["retry_count"])
                stats["scrape_failed"] += 1
                return
                
            SPAM_DOMAINS = ["bloguerosa.com", "qodsblog.com", "blogdeazar.com", "blazingblog.com"]
            if any(spam in link for spam in SPAM_DOMAINS):
                logger.info("  [Skip] Spam domain filtered.")
                db.update_event_scrape(doc_id, status="scrape_failed", scrape_error="spam domain")
                stats["spam_filtered"] += 1
                return

            result = await scrape_url(link, browser)

            if not result:
                current_data["retry_count"] = retry_count + 1
                db.update_event_scrape(doc_id, status="scrape_failed",
                                       scrape_error="all layers failed",
                                       retry_count=current_data["retry_count"])
                stats["scrape_failed"] += 1
                return

            ok = db.update_event_scrape(
                doc_id,
                status="scraped",
                scraped_page_title=result["page_title"],
                scraped_meta_description=result["meta_description"],
                scraped_full_text=result["text"],
                scrape_layer=result["layer"],
            )
            stats["scraped_ok"] += 1
            layer = result["layer"]
            if layer in stats["scrape_layers"]:
                stats["scrape_layers"][layer] += 1
            logger.info(f"  [{'saved' if ok else 'warn: no match'}:{link[:40]}] "
                        f"layer={result['layer']} words={len(result['text'].split())}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await asyncio.gather(*[scrape_one(doc, browser) for doc in pending])
        await browser.close()

    db.close()
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] scraper done.")
    return stats


 # ── RSS Feed scraper ──────────────────────────────────────────────────────────
async def run_rss_agent():
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] RSS scraper starting...")
    db        = DatabaseFacade()
    seen_urls = set()
    seen_titles: list[set] = []  # List of (word_set, title_str) for duplicate detection
    semaphore = asyncio.Semaphore(5)

    stats = {
        "feeds_processed": 0,
        "entries_found": 0,
        "items_inserted": 0,
        "duplicates_skipped": 0,
        "near_duplicates_skipped": 0,
        "scrape_failures": 0,
        "scrape_layers": {"L1": 0, "Firecrawl": 0, "L2": 0},
    }

    SPAM_DOMAINS = ["bloguerosa.com", "qodsblog.com", "blogdeazar.com", "blazingblog.com"]

    def _title_words(title: str) -> set:
        """Extract lowercase word set from a title for similarity comparison."""
        return set(title.lower().split()) - {"the", "a", "an", "is", "in", "on", "of", "and", "to", "for", "with", "at", "by"}

    def _is_near_duplicate(title: str) -> bool:
        """Check if a title is >80% similar (Jaccard) to any previously seen title."""
        words = _title_words(title)
        if len(words) < 3:
            return False
        for seen_words, _ in seen_titles:
            if not seen_words:
                continue
            intersection = words & seen_words
            union = words | seen_words
            if len(union) > 0 and len(intersection) / len(union) > 0.8:
                return True
        return False

    async def process_rss_feed(feed_url, browser, ig_category):
        try:
            logger.info(f"Fetching RSS: {feed_url} [{ig_category}]")
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                         headers={"User-Agent": USER_AGENT}) as client:
                r = await client.get(feed_url)
                r.raise_for_status()
                feed_data = r.content

            feed = feedparser.parse(feed_data)
            stats["feeds_processed"] += 1

            if not feed.entries:
                logger.info(f"[RSS Skip] {feed_url} -> 0 entries returned")
                return

            stats["entries_found"] += len(feed.entries[:5])
            logger.info(f"[RSS] {feed_url} -> {len(feed.entries)} entries, processing top 5")

            for entry in feed.entries[:5]:
                async with semaphore:
                    link  = entry.get("link")
                    title = entry.get("title", "No Title")

                    if not link:
                        continue
                    if link in seen_urls:
                        stats["duplicates_skipped"] += 1
                        continue
                    seen_urls.add(link)

                    # Near-duplicate detection across feeds
                    if _is_near_duplicate(title):
                        logger.info(f"  [Skip] Near-duplicate title: {title[:60]}")
                        stats["near_duplicates_skipped"] += 1
                        continue
                    seen_titles.append((_title_words(title), title))

                    if db.link_exists(link, ig_category):
                        logger.info(f"  [Skip] Already in DB: {link[:60]}")
                        stats["duplicates_skipped"] += 1
                        continue

                    logger.info(f"\n── RSS Item: {title} | {link}")

                    if any(spam in link for spam in SPAM_DOMAINS):
                        logger.info("  [Skip] Spam domain filtered.")
                        continue

                    result = await scrape_url(link, browser)
                    if not result:
                        logger.info(f"  [Fail] RSS item scrape failed: {link[:50]}")
                        stats["scrape_failures"] += 1
                        continue

                    # Track scrape layer
                    layer = result.get("layer", "L1")
                    if layer in stats["scrape_layers"]:
                        stats["scrape_layers"][layer] += 1

                    # Build data dict with RSS summary and category for downstream agents
                    rss_summary = entry.get("summary", "")
                    if rss_summary:
                        from bs4 import BeautifulSoup as _BS
                        rss_summary = _BS(rss_summary, "html.parser").get_text(separator=' ', strip=True)

                    # Extract published timestamp for recency bonus in intelligence agent
                    import calendar
                    published_at = None
                    pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                    if pub_parsed:
                        try:
                            published_at = calendar.timegm(pub_parsed)
                        except Exception:
                            pass

                    item_data = {
                        "summary": rss_summary,
                        "category": "News",
                        "ig_tags": [ig_category],
                        "scraped_full_text": result["text"],
                        "scraped_page_title": result["page_title"],
                        "scraped_meta_description": result["meta_description"],
                    }
                    if published_at is not None:
                        item_data["published_at"] = published_at

                    doc_id = db.scrapes.insert_queue(
                        title, link, ig_category, "RSS", "scraped", data=item_data
                    )
                    if doc_id:
                        stats["items_inserted"] += 1
                        logger.info(f"  [saved:{link[:40]}] "
                                    f"(RSS) layer={result['layer']} words={len(result['text'].split())}")
        except Exception as e:
            logger.info(f"[RSS Error] {feed_url} -> {e}")

    from src.config.sources import ALL_RSS_FEEDS

    # Normalize IG categories from sources.py as safety net
    normalized_feeds = {}
    for ig_key, feeds in ALL_RSS_FEEDS.items():
        canonical = normalize_ig(ig_key) or ig_key
        normalized_feeds[canonical] = feeds

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        logger.info("\n--- Scraping RSS Feeds ---")
        tasks = []
        for ig_category, feeds in normalized_feeds.items():
            for feed_url in feeds:
                tasks.append(process_rss_feed(feed_url, browser, ig_category))
        await asyncio.gather(*tasks)
        await browser.close()

    db.close()
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] RSS scraper done.")
    return stats


if __name__ == "__main__":
    asyncio.run(run_scraper_agent())

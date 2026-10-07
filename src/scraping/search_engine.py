import logging
logger = logging.getLogger(__name__)

from pathlib import Path

from ddgs import DDGS
import requests
import time
import os 
from dotenv import load_dotenv
from src.db.postgres_database import DatabaseFacade

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

#ig_mappings and categories are passed from the scraping_main.py file

def run_search_agent(ig_mappings, categories, max_result=5):
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting scheduled search agent...")
    db = DatabaseFacade()

    logger.info("--------- Keyword Expansion -------------")
    search_queries = []   #search_queries = [{"query": "keyword category", "ig": "ig_key"}]
    for keyword, ig_key in ig_mappings.items():
        for category in categories:
            query = f"{keyword} {category}"
            search_queries.append({"query": query, "ig": ig_key})
            logger.info(f"- {query}")

    logger.info("\n--- Searching DuckDuckGo ---")
     
    ddgs = DDGS()
    for search_item in search_queries:
        query = search_item["query"]
        ig_key = search_item["ig"]
        logger.info(f"\nResults for '{query}':")
        try:
            results = []
            source_engine = 'DuckDuckGo'
            
            try:
                # Setting safesearch='moderate' and timelimit='y' helps filter out obscure/spam domains.
                results = list(ddgs.text(query, max_results=max_result, safesearch='moderate', timelimit='y'))
            except Exception as ddg_error:
                logger.info(f"   [!] DuckDuckGo failed ({ddg_error}). Falling back to Tavily...")
                source_engine = 'Tavily'
                time.sleep(2)  # Avoid fast consecutive requests
                try:
                    results = get_tavily_results(query, max_result)
                except Exception as t_error:
                    logger.info(f"   [!] Tavily Search also failed: {t_error}")

            if not results:
                logger.info("   No results found.")
            else:
                SPAM_DOMAINS = ["bloguerosa.com", "qodsblog.com", "blogdeazar.com", "blazingblog.com", "youtube.com", "facebook.com", "instagram.com",
    "tiktok.com"]
                for i, result in enumerate(results, start=1):
                    title = result.get('title', 'No Title')
                    link = result.get('href', result.get('url', 'No Link'))
                    
                    # Ensure high quality by filtering spam domains and low-reputation TLDs
                    is_spam = any(spam in link for spam in SPAM_DOMAINS)
                    is_low_quality = any(link.endswith(tld) or (tld + "/") in link for tld in [".xyz", ".info", ".top", ".cc", ".biz"])
                    if is_spam or is_low_quality:
                        logger.info(f"{i}. [Skip] Filtered low-quality or spam domain: {link}")
                        continue

                    logger.info(f"{i}. [{source_engine}] {title}")
                    logger.info(f"   Link: {link}")
                    
                    # Inserting by checking the link along with ig
                    if not db.link_exists(link, ig_key):
                        db.insert_event(title, link, ig_key, source_engine, 'not processed')
        except Exception as e:
            logger.info(f"   Error searching for '{query}': {e}")
        
        time.sleep(1)
             
    db.close()
 
# Searching using Tavily Search API
def get_tavily_results(query, max_results=5):
    """Fetch results from Tavily Search API (fallback when DuckDuckGo fails)."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or api_key == "your_tavily_api_key_here":
        logger.info("   [!] Tavily API key not configured in .env")
        logger.info("   [!] Get your API key at https://app.tavily.com and add it to .env")
        return []
    
    url = "https://api.tavily.com/search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "query": query,
        "search_depth": "advanced",
        "include_answer": False,
        "max_results": max_results
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get('results', [])[:max_results]:
        results.append({
            'title': item.get('title', 'No Title'),
            'url': item.get('url', 'No Link')
        })
    return results


def run_registry_search(db, query_index, max_results=3):
    """Run web discovery from a deduplicated registry search plan.

    query_index maps one query string to [(ig, category), ...] (see
    shared_query_index); each query string is executed once and its results
    attributed to every listed IG. Canonical URL deduplication is per
    (link, IG) so one event can acquire multiple valid IG associations.
    Returns a stats dict for phase reporting.
    """
    from src.utils.ig_normalizer import normalize_ig

    stats = {
        "queries_requested": len(query_index or {}),
        "queries_run": 0,
        "igs_covered": 0,
        "items_inserted": 0,
        "duplicates_skipped": 0,
        # Provider-attempt diagnostics (aggregate counts, not payloads).
        "provider_calls": {"ddg": 0, "tavily": 0},
        "tavily_fallbacks": 0,
    }
    if not query_index:
        logger.info("Registry search plan is empty; nothing to discover.")
        return stats

    try:
        from ddgs import DDGS
        ddgs = DDGS()
    except Exception as e:
        logger.info(f"   [!] Search provider unavailable ({e}). Skipping registry search.")
        return stats

    SPAM_DOMAINS = ["bloguerosa.com", "qodsblog.com", "blogdeazar.com", "blazingblog.com",
                    "youtube.com", "facebook.com", "instagram.com", "tiktok.com"]
    LOW_TLDS = [".xyz", ".info", ".top", ".cc", ".biz"]
    covered = set()

    for query, owners in (query_index or {}).items():
        # Normalize owner IGs; drop unresolvable ones (reported, not invented).
        targets = []
        for ig_name, category in owners or []:
            canonical = normalize_ig(ig_name) or ig_name
            if not canonical:
                logger.info(f"   [Skip] Unresolvable IG '{ig_name}' for query '{query}'.")
                continue
            targets.append((canonical, str(category).capitalize()))
        if not targets:
            continue
        stats["queries_run"] += 1
        logger.info(f"\nResults for '{query}' (IGs: {', '.join(sorted({t[0] for t in targets}))}):")
        try:
            results = []
            source_engine = 'DuckDuckGo'
            try:
                results = list(ddgs.text(query, max_results=max_results,
                                         safesearch='moderate', timelimit='y'))
                stats["provider_calls"]["ddg"] += 1
            except Exception as ddg_error:
                logger.info(f"   [!] DuckDuckGo failed ({ddg_error}). Falling back to Tavily...")
                source_engine = 'Tavily'
                stats["tavily_fallbacks"] += 1
                time.sleep(2)
                try:
                    results = get_tavily_results(query, max_results)
                    stats["provider_calls"]["tavily"] += 1
                except Exception as t_error:
                    logger.info(f"   [!] Tavily Search also failed: {t_error}")
            if not results:
                logger.info("   No results found.")
            else:
                for result in results:
                    title = result.get('title', 'No Title')
                    link = result.get('href', result.get('url', 'No Link'))
                    if not link or link == 'No Link':
                        continue
                    if any(spam in link for spam in SPAM_DOMAINS):
                        continue
                    if any(link.endswith(tld) or (tld + "/") in link for tld in LOW_TLDS):
                        continue
                    summary = result.get('body', result.get('content', result.get('snippet', '')))
                    for ig_key, category in targets:
                        covered.add(ig_key)
                        if db.link_exists(link, ig_key):
                            stats["duplicates_skipped"] += 1
                            continue
                        if db.insert_event(title, link, ig_key, source_engine, 'not processed'):
                            stats["items_inserted"] += 1
        except Exception as e:
            logger.info(f"   Error searching for '{query}': {e}")
        time.sleep(1)

    stats["igs_covered"] = len(covered)
    logger.info(f"Registry search done: {stats['queries_run']} queries, "
                f"{stats['items_inserted']} inserted across {stats['igs_covered']} IGs.")
    return stats


def build_search_dry_run(registry, max_queries_per_ig, max_docs_to_intelligence,
                         search_enabled=False):
    """Describe what a registry search run WOULD do. No network, no DB.

    Returns aggregate counts only: active IGs and readiness, unique queries
    after deduplication, expected provider query count, the Intelligence
    admission cap, and groups lacking delivery destinations. Never executes
    provider calls regardless of the enabled flag.
    """
    from src.config.interest_groups import shared_query_index

    active = registry.all_active_names()
    plan = registry.search_plan(max_queries_per_ig)
    index = shared_query_index(plan)
    readiness = registry.discovery_readiness()
    without_queries = sorted(set(active) - set(plan.keys()))
    without_destinations = sorted(
        name for name in active
        if not (readiness.get(name, {}).get("zulip")
                or readiness.get(name, {}).get("email"))
    )
    return {
        "search_enabled": bool(search_enabled),
        "igs_configured": len(active),
        "igs_ready": len(plan),
        "igs_without_queries": without_queries,
        "unique_queries": len(index),
        "expected_query_count": len(index),
        "max_docs_to_intelligence": max_docs_to_intelligence,
        "igs_without_destinations": without_destinations,
    }


def main():
    logger.info("Search agent started. Running search...")
    logger.info("Press Ctrl+C to exit.")
    
    # We map the search terms dynamically to the actual IG email groups
    ig_mappings = {
        "Artificial intelligence": "ai",
        "web development": "web development",
        "Data science": "data science"
    }
    categories = ["internships", "Current news", "workshops", "events", "hackathons"]
    
    # Run the search agent immediately
    run_search_agent(ig_mappings, categories)
 
if __name__ == "__main__":
    main()
 

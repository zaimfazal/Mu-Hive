import asyncio
import aiohttp
from src.agents.planner import MVP_IGS
import feedparser
import logging
import time
import os
from bs4 import BeautifulSoup
from readability import Document
import httpx
import trafilatura
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import requests
from duckduckgo_search import DDGS
import calendar

from src.db.postgres_database import DatabaseFacade as Database
from src.config.sources import ALL_RSS_FEEDS
from src.config.logging_config import setup_logging

logger = logging.getLogger(__name__)

# --- Config for Scraper ---
MIN_WORDS = 120
SCRAPE_LIMIT = 50
TIMEOUT = 30
PW_WAIT_MS = 4000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
feedparser.USER_AGENT = USER_AGENT

try:
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module='newspaper')
    from newspaper import Article as NArticle
    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False

# --- Curated, IG-specific search queries for relevance ---
SEARCH_QUERIES = {
    "AI": {
        "news": [
            "artificial intelligence AI breakthroughs 2026",
            "LLM large language model releases news",
            "generative AI industry updates",
        ],
        "hackathons": ["AI machine learning hackathon 2026"],
    },
    "Web Development": {
        "news": [
            "web development JavaScript React framework news 2026",
            "frontend backend web dev trends",
        ],
        "hackathons": ["web development hackathon frontend backend 2026"],
    },
    "UI/UX": {
        "news": [
            "UX design trends user experience 2026",
            "Figma UI design product design updates",
        ],
        "hackathons": ["UI UX design hackathon designathon 2026"],
    },
    "Cyber Security": {
        "news": [
            "cybersecurity vulnerability zero-day threat 2026",
            "infosec security breach advisory news",
        ],
        "hackathons": ["CTF capture the flag cybersecurity hackathon 2026"],
    },
    "Data Science": {
        "news": [
            "data science analytics trends 2026",
            "big data engineering visualization news",
        ],
        "hackathons": ["data science analytics Kaggle hackathon 2026"],
    },
}


def clean_html(html_content):
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=' ', strip=True)


def run_rss_scout(db: Database):
    logger.info("Running RSS Scout...")
    new_count = 0
    seen_titles: list[tuple] = []  # (word_set, title) for duplicate detection

    def _title_words(title: str) -> set:
        return set(title.lower().split()) - {"the", "a", "an", "is", "in", "on", "of", "and", "to", "for", "with", "at", "by"}

    def _is_near_duplicate(title: str) -> bool:
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

    for ig, feeds in ALL_RSS_FEEDS.items():
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                if not feed.entries:
                    continue
                for entry in feed.entries[:5]:
                    pub_parsed = entry.get(
                        'published_parsed') or entry.get('updated_parsed')
                    if pub_parsed:
                        pub_time = calendar.timegm(pub_parsed)
                        if time.time() - pub_time > 86400:
                            continue

                    title = entry.get("title", "").strip()
                    link = entry.get("link", "").strip()

                    if not title or not link:
                        continue

                    # Near-duplicate detection
                    if _is_near_duplicate(title):
                        logger.info(f"  [Skip] Near-duplicate: {title[:60]}")
                        continue
                    seen_titles.append((_title_words(title), title))

                    summary_raw = entry.get("summary", "")
                    if not summary_raw and "content" in entry:
                        summary_raw = entry.content[0].value
                    summary = clean_html(summary_raw)

                    if db.insert_opportunity(title, link, summary, source_engine="RSS", ig_tags=[ig], category="News"):
                        new_count += 1
            except Exception as e:
                logger.error(f"RSS error on {feed_url}: {e}")
    logger.info(f"RSS Scout inserted {new_count} new opportunities.")


def get_tavily_results(query, max_results=3):
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        response = requests.post("https://api.tavily.com/search", json={
            "query": query, "search_depth": "advanced", "max_results": max_results
        }, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        return response.json().get('results', [])[:max_results]
    except:
        return []


def run_search_agent(db: Database):
    """Search engine agent using curated, IG-specific queries for relevance."""
    logger.info("Running Search Engine Agent...")

    ddgs = DDGS()
    SPAM_DOMAINS = [
        "bloguerosa.com", "qodsblog.com", "blogdeazar.com",
        "youtube.com", "facebook.com", "instagram.com", "tiktok.com",
        "pinterest.com", "reddit.com", "quora.com"
    ]
    new_count = 0

    for ig, categories in SEARCH_QUERIES.items():
        for category, queries in categories.items():
            for query in queries:
                try:
                    try:
                        results = list(
                            ddgs.text(query, max_results=3, safesearch='moderate', timelimit='d'))
                        source = 'DuckDuckGo'
                    except Exception:
                        results = get_tavily_results(query, 3)
                        source = 'Tavily'

                    if not results:
                        continue

                    for result in results:
                        title = result.get('title', 'No Title')
                        link = result.get('href', result.get('url', ''))
                        if not link:
                            continue
                        if any(spam in link for spam in SPAM_DOMAINS) or link.endswith(('.xyz', '.info')):
                            continue
                        # Extract summary/snippet from search results
                        summary = result.get('body', result.get(
                            'content', result.get('snippet', '')))
                        if db.insert_opportunity(title, link, summary=summary, source_engine=source, ig_tags=[ig], category=category.capitalize()):
                            new_count += 1
                except Exception as e:
                    logger.warning(f"Error searching '{query}': {e}")
                time.sleep(1)
    logger.info(f"Search Engine inserted {new_count} new opportunities.")


def extract(html: str) -> str:
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
            art = NArticle("")
            art.set_html(html)
            art.parse()
            candidates.append(art.text or "")
        except Exception:
            pass
    return max(candidates, key=lambda t: len(t.split()), default="")


# --- HACKATHON APIs ---
# API Endpoints
DEVFOLIO_API = "https://api.devfolio.co/api/search/hackathons"
UNSTOP_API = "https://unstop.com/api/public/opportunity/search-result"
DEVPOST_API = "https://devpost.com/api/hackathons"
HACKEREARTH_API = "https://www.hackerearth.com/api/events/upcoming/"


def normalize_event(name, platform, link, start, end, tags, location="", prize="", cost="", elig="", description=""):
    """Normalize a hackathon event, extracting tag names from dict-style tags."""
    cleaned_tags = []
    if isinstance(tags, list):
        for t in tags:
            if not t:
                continue
            if isinstance(t, dict):
                # Extract 'name' field from API tag objects (Devfolio, Unstop, etc.)
                tag_name = t.get("name", t.get("title", ""))
                if tag_name:
                    cleaned_tags.append(str(tag_name).strip())
            else:
                cleaned_tags.append(str(t).strip())

    # Strip HTML from prize pool (Devpost returns HTML-wrapped values)
    if prize:
        prize = clean_html(str(prize))

    return {
        "eventName": str(name).strip() if name else "Unknown",
        "platform": platform,
        "registrationLink": link if link else "",
        "startDate": str(start).strip() if start else "TBA",
        "endDate": str(end).strip() if end else "TBA",
        "tags": cleaned_tags,
        "location": str(location).strip() if location else "Online",
        "prizePool": prize if prize else "",
        "cost": str(cost).strip() if cost else "Free",
        "eligibility": str(elig).strip() if elig else "Students",
        "description": str(description).strip() if description else ""
    }


async def fetch_devfolio_page(session, semaphore, offset):
    events = []
    payload = {"from": offset, "size": 50, "query": {"match_all": {}}}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async with semaphore:
        try:
            async with session.post(DEVFOLIO_API, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    hits = data.get("hits", {}).get("hits", [])
                    for item in hits:
                        try:
                            src = item.get("_source", {})
                            name = src.get("name", "Unknown")
                            slug = src.get("slug", "")
                            link = f"https://{slug}.devfolio.co" if slug else ""
                            start = src.get("starts_at", "TBA")
                            end = src.get("ends_at", "TBA")
                            tags = src.get("themes", [])
                            loc = "Online" if src.get(
                                "is_online") else "In-Person"
                            # Extract tagline/desc
                            desc_val = src.get("tagline") or src.get("desc") or ""
                            desc_val = clean_html(desc_val).strip()
                            if len(desc_val) > 250:
                                desc_val = desc_val[:247] + "..."
                            events.append(normalize_event(
                                name, "Devfolio", link, start, end, tags, loc, "TBA", "Free", "Students", description=desc_val))
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Devfolio page fetch error (offset {offset}): {e}")
    return events


async def get_all_devfolio(session, semaphore):
    logger.info("Starting Devfolio API concurrent extraction...")
    offset = 0
    all_events = []
    while offset < 2500:
        tasks = [fetch_devfolio_page(session, semaphore, o)
                 for o in range(offset, offset + 250, 50)]
        results = await asyncio.gather(*tasks)

        empty_page_found = False
        for res in results:
            if not res:
                empty_page_found = True
            all_events.extend(res)

        if empty_page_found:
            break
        offset += 250
    logger.info(f"Devfolio complete. Found {len(all_events)} events.")
    return all_events


async def fetch_unstop_page(session, semaphore, page):
    events = []
    url = f"{UNSTOP_API}?opportunity=hackathons&page={page}&per_page=20"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    arr = data.get("data", {}).get("data", [])
                    for item in arr:
                        try:
                            name = item.get("title", "")
                            seo = item.get("seo_url", "")
                            link = f"https://unstop.com/hackathons/{
                                seo}" if seo else ""
                            start = item.get("start_date", "TBA")
                            end = item.get("end_date", "TBA")
                            filters = item.get("filters", {})
                            tags = [t.get("name") for t in filters] if isinstance(
                                filters, list) else []
                            cost = "Paid" if item.get(
                                "payment_type") == "paid" else "Free"
                            desc_val = clean_html(item.get("details", "")).strip()
                            if len(desc_val) > 250:
                                desc_val = desc_val[:247] + "..."
                            events.append(normalize_event(
                                name, "Unstop", link, start, end, tags, "Virtual/Online", "TBA", cost, "Students/College", description=desc_val))
                        except Exception:
                            continue
        except Exception:
            pass
    return events


async def get_all_unstop(session, semaphore):
    logger.info("Starting Unstop API concurrent extraction...")
    page = 1
    all_events = []
    while page < 50:
        tasks = [fetch_unstop_page(session, semaphore, p)
                 for p in range(page, page + 5)]
        results = await asyncio.gather(*tasks)

        empty_page_found = False
        for res in results:
            if not res:
                empty_page_found = True
            all_events.extend(res)

        if empty_page_found:
            break
        page += 5
    logger.info(f"Unstop complete. Found {len(all_events)} events.")
    return all_events


async def fetch_devpost_page(session, semaphore, page):
    events = []
    url = f"{DEVPOST_API}?page={page}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    arr = data.get("hackathons", [])
                    for item in arr:
                        try:
                            name = item.get("title", "")
                            link = item.get("url", "")
                            start = item.get("submission_period_dates", "TBA")
                            end = item.get("submission_period_dates", "TBA")
                            tags = item.get("themes", [])
                            loc = item.get("location", "Online")
                            prize = item.get("prize_amount", "")
                            events.append(normalize_event(
                                name, "Devpost", link, start, end, tags, loc, prize, "Free", "Students/Global"))
                        except Exception:
                            continue
        except Exception:
            pass
    return events


async def get_all_devpost(session, semaphore):
    logger.info("Starting Devpost API concurrent extraction...")
    page = 1
    all_events = []
    while page < 50:
        # FIX: tasks variable was undefined — now properly creates page fetch tasks
        tasks = [fetch_devpost_page(session, semaphore, p)
                 for p in range(page, page + 5)]
        results = await asyncio.gather(*tasks)

        empty_page_found = False
        for res in results:
            if not res:
                empty_page_found = True
            all_events.extend(res)

        if empty_page_found:
            break
        page += 5
    logger.info(f"Devpost complete. Found {len(all_events)} events.")
    return all_events


async def fetch_hackerearth_page(session, semaphore, page):
    events = []
    url = f"{HACKEREARTH_API}?page={page}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    arr = data.get("response", [])
                    for item in arr:
                        try:
                            name = item.get("title", "")
                            link = item.get("url", "")
                            start = item.get("start_utc_tz", "TBA")
                            end = item.get("end_utc_tz", "TBA")
                            tags = item.get("tags", [])
                            loc = item.get("location", "Online")
                            desc_val = clean_html(item.get("description", "")).strip()
                            if len(desc_val) > 250:
                                desc_val = desc_val[:247] + "..."
                            events.append(normalize_event(
                                name, "HackerEarth", link, start, end, tags, loc, "TBA", "Free", "Open", description=desc_val))
                        except Exception:
                            continue
        except Exception:
            pass
    return events


async def get_all_hackerearth(session, semaphore):
    logger.info("Starting HackerEarth API concurrent extraction...")
    page = 1
    all_events = []
    while page < 50:
        # FIX: tasks variable was undefined — now properly creates page fetch tasks
        tasks = [fetch_hackerearth_page(session, semaphore, p)
                 for p in range(page, page + 5)]
        results = await asyncio.gather(*tasks)

        empty_page_found = False
        for res in results:
            if not res:
                empty_page_found = True
            all_events.extend(res)

        if empty_page_found:
            break
        page += 5
    logger.info(f"HackerEarth complete. Found {len(all_events)} events.")
    return all_events


def map_hackathon_tags(tags):
    """
    Map hackathon tags to Interest Groups using precise keyword matching.
    Uses specific multi-word phrases to avoid false positives.
    """
    tag_map = {
        # AI / Machine Learning
        "artificial intelligence": "AI",
        "machine learning": "AI",
        "deep learning": "AI",
        "genai": "AI",
        "gen ai": "AI",
        "generative ai": "AI",
        "llm": "AI",
        "natural language processing": "AI",
        "nlp": "AI",
        "computer vision": "AI",
        "neural network": "AI",
        "chatbot": "AI",
        "tensorflow": "AI",
        "pytorch": "AI",
        "hugging face": "AI",
        # Data Science
        "data science": "Data Science",
        "data analytics": "Data Science",
        "big data": "Data Science",
        "data engineering": "Data Science",
        "data visualization": "Data Science",
        "kaggle": "Data Science",
        "statistics": "Data Science",
        "pandas": "Data Science",
        # Web Development
        "web development": "Web Development",
        "web dev": "Web Development",
        "frontend": "Web Development",
        "front-end": "Web Development",
        "full stack": "Web Development",
        "fullstack": "Web Development",
        "backend": "Web Development",
        "back-end": "Web Development",
        "react": "Web Development",
        "reactjs": "Web Development",
        "nextjs": "Web Development",
        "next.js": "Web Development",
        "node.js": "Web Development",
        "nodejs": "Web Development",
        "javascript": "Web Development",
        "typescript": "Web Development",
        "django": "Web Development",
        "flask": "Web Development",
        "vue": "Web Development",
        "angular": "Web Development",
        "html": "Web Development",
        "css": "Web Development",
        "svelte": "Web Development",
        # Cyber Security
        "cybersecurity": "Cyber Security",
        "cyber security": "Cyber Security",
        "infosec": "Cyber Security",
        "information security": "Cyber Security",
        "penetration testing": "Cyber Security",
        "pen testing": "Cyber Security",
        "ethical hacking": "Cyber Security",
        "ctf": "Cyber Security",
        "capture the flag": "Cyber Security",
        "network security": "Cyber Security",
        "malware": "Cyber Security",
        "vulnerability": "Cyber Security",
        "soc": "Cyber Security",
        "threat intelligence": "Cyber Security",
        # UI/UX
        "ui/ux": "UI/UX",
        "ui ux": "UI/UX",
        "user experience": "UI/UX",
        "user interface": "UI/UX",
        "ux design": "UI/UX",
        "ui design": "UI/UX",
        "product design": "UI/UX",
        "interaction design": "UI/UX",
        "figma": "UI/UX",
        "prototyping": "UI/UX",
        "wireframe": "UI/UX",
        "usability": "UI/UX",
    }
    mapped_igs = set()
    for tag in tags:
        t = str(tag).lower().strip()
        for keyword, ig in tag_map.items():
            if keyword in t:
                mapped_igs.add(ig)
    return list(mapped_igs)


def _compute_tag_confidence_score(ig_tags, tags):
    """
    Compute a quality score based on how many tags matched IG keywords.
    Strong match (2+ mapped IGs or 3+ matching keywords) = 7
    Weak match (1 mapped IG) = 6
    """
    if len(ig_tags) >= 2:
        return 7
    # Count how many raw tags matched
    tag_map_keys = [
        "artificial intelligence", "machine learning", "deep learning", "genai",
        "data science", "data analytics", "big data",
        "web development", "frontend", "full stack", "react", "javascript",
        "cybersecurity", "ethical hacking", "ctf", "penetration testing",
        "ui/ux", "ux design", "ui design", "figma", "product design",
    ]
    match_count = sum(1 for tag in tags if any(
        k in str(tag).lower() for k in tag_map_keys))
    return 7 if match_count >= 3 else 6


async def run_hackathon_apis(db: Database):
    logger.info("Running Hackathon API Scrapers...")
    events = []
    semaphore = asyncio.Semaphore(15)
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            get_all_devfolio(session, semaphore),
            get_all_unstop(session, semaphore),
            get_all_devpost(session, semaphore),
            get_all_hackerearth(session, semaphore)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                events.extend(res)
            elif isinstance(res, Exception):
                logger.warning(f"Hackathon extraction error: {res}")

    new_count = 0
    for ev in events:
        summary = (
            f"Platform: {ev['platform']}\n"
            f"Start: {ev['startDate']}\n"
            f"End: {ev['endDate']}\n"
            f"Location: {ev['location']}\n"
            f"Prize Pool: {ev['prizePool']}\n"
            f"Cost: {ev['cost']}\n"
            f"Eligibility: {ev['eligibility']}\n"
            f"Tags: {', '.join(ev['tags'])}"
        )
        desc = ev.get("description", "").strip()
        if desc:
            summary += f"\nDescription: {desc}"
        ig_tags = map_hackathon_tags(ev['tags'])

        # Only insert if there is at least one mapped IG
        if ig_tags:
            score = _compute_tag_confidence_score(ig_tags, ev['tags'])
            if db.insert_opportunity(ev['eventName'], ev['registrationLink'], summary, source_engine=ev['platform'], ig_tags=ig_tags, category="Hackathons", is_processed=True, quality_score=score):
                new_count += 1

    logger.info(f"Hackathon APIs inserted {
                new_count} new targeted opportunities.")

# -----------------------------


async def l1_httpx(url: str):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        title = soup.title.string if soup.title else ""
        meta = soup.find("meta", attrs={"name": "description"})
        meta = meta["content"] if meta else ""
        text = extract(r.text)
        return (text, title, meta) if len(text.split()) >= MIN_WORDS else None
    except Exception as e:
        return None


async def l2_playwright(url: str, browser):
    try:
        page = await (await browser.new_context(user_agent=USER_AGENT)).new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
        await page.wait_for_timeout(PW_WAIT_MS)
        html = await page.content()
        title = await page.title()
        try:
            meta = await page.evaluate('document.querySelector("meta[name=\'description\']")?.content || ""')
        except:
            meta = ""
        await page.close()
        text = extract(html)
        return (text, title, meta) if len(text.split()) >= MIN_WORDS else None
    except Exception as e:
        return None


async def run_scraper_agent(db: Database):
    logger.info("Running Scraper Layer...")
    pending = db.find_pending_scrape(limit=SCRAPE_LIMIT)
    if not pending:
        logger.info("No items pending scrape.")
        return

    logger.info(f"{len(pending)} items pending scrape.")
    semaphore = asyncio.Semaphore(5)

    async def scrape_one(doc, browser):
        async with semaphore:
            doc_id, link = doc["_id"], doc.get("link")
            result = await l1_httpx(link)
            label = "L1"
            if not result:
                result = await l2_playwright(link, browser)
                label = "L2"

            if result:
                text, title, meta = result
                db.update_event_scrape(doc_id, status="scraped", scraped_page_title=title,
                                       scraped_meta_description=meta, scraped_full_text=text, scrape_layer=label)
            else:
                db.update_event_scrape(
                    doc_id, status="scrape_failed", scrape_error="all layers failed")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await asyncio.gather(*[scrape_one(doc, browser) for doc in pending])
        await browser.close()
    logger.info("Scraper Layer finished.")


async def run_scout_async():
    db = Database()
    run_rss_scout(db)
    run_search_agent(db)
    await run_hackathon_apis(db)
    await run_scraper_agent(db)
    db.close()


def run_scout():
    asyncio.run(run_scout_async())


if __name__ == "__main__":
    setup_logging()
    run_scout()

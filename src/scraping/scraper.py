"""
src/scraping/scraper.py
=======================
Fetches hackathon events from all platforms concurrently.

Public API:
    async def fetch_all_events() -> list[dict]

Each platform has an internal paginator. All results are returned as a flat
list of canonical event dicts (via normalize_event). No filtering or scoring
happens here — this layer only fetches.
"""


import asyncio
import aiohttp
import feedparser
import logging
from src.config.sources import ALL_RSS_FEEDS

from src.config.constants import (
    USER_AGENT, CONCURRENCY_LIMIT, SESSION_TIMEOUT,
    DEVFOLIO_API, DEVPOST_API, UNSTOP_API, HACKEREARTH_API,
    IG_KEYWORDS, MASTER_IGS,
)
from src.scraping.data_cleaner import clean_events
from src.scraping.curate import curate
from src.db.data_schemas import Event
from src.db.postgres_database import DatabaseFacade

# ---------------------------------------------------------------------------
# Canonical event shape
# ---------------------------------------------------------------------------

def normalize_event(
    name: str,
    platform: str,
    link: str,
    start: str,
    end: str,
    tags: list,
    location: str = "",
    prize: str = "",
    cost: str = "",
    elig: str = "",
) -> dict:
    """Return a canonical event dict from raw API fields."""
    return {
        "eventName":        str(name).strip() if name else "Unknown",
        "platform":         platform,
        "registrationLink": str(link).strip() if link else "",
        "startDate":        str(start).strip() if start else "TBA",
        "endDate":          str(end).strip() if end else "TBA",
        "tags":             [str(t).strip() for t in tags if t] if isinstance(tags, list) else [],
        "location":         str(location).strip() if location else "Online",
        "prizePool":        str(prize).strip() if prize else "",
        "cost":             str(cost).strip() if cost else "Free",
        "eligibility":      str(elig).strip() if elig else "Students",
    }


# ---------------------------------------------------------------------------
# Devfolio
# ---------------------------------------------------------------------------

async def _devfolio_page(session, sem, offset: int) -> list[dict]:
    payload = {"from": offset, "size": 50, "query": {"match_all": {}}}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[dict] = []
    async with sem:
        try:
            async with session.post(DEVFOLIO_API, json=payload, headers=headers) as r:
                if r.status != 200:
                    return events
                data = await r.json(content_type=None)
                for item in data.get("hits", {}).get("hits", []):
                    try:
                        src  = item.get("_source", {})
                        slug = src.get("slug", "")
                        events.append(normalize_event(
                            name     = src.get("name", "Unknown"),
                            platform = "Devfolio",
                            link     = f"https://{slug}.devfolio.co" if slug else "",
                            start    = src.get("starts_at", "TBA"),
                            end      = src.get("ends_at", "TBA"),
                            tags     = src.get("themes", []),
                            location = "Online" if src.get("is_online") else "In-Person",
                            cost     = "Free",
                            elig     = "Students",
                        ))
                    except Exception as e:
                        print(f"[Error] {e}")
                        continue
        except Exception as e:
            print(f"[Error] {e}")
    return events


async def _fetch_devfolio(session, sem) -> list[dict]:
    offset, all_events, seen = 0, [], set()
    while True:
        pages = await asyncio.gather(
            *[_devfolio_page(session, sem, o) for o in range(offset, offset + 500, 50)]
        )
        new = False
        for page in pages:
            if not page:
                return all_events
            for e in page:
                if e["registrationLink"] and e["registrationLink"] not in seen:
                    seen.add(e["registrationLink"])
                    all_events.append(e)
                    new = True
        if not new:
            break
        offset += 500
    return all_events


# ---------------------------------------------------------------------------
# Devpost
# ---------------------------------------------------------------------------

async def _devpost_page(session, sem, page: int) -> list[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[dict] = []
    async with sem:
        try:
            async with session.get(f"{DEVPOST_API}&page={page}", headers=headers) as r:
                if r.status != 200:
                    return events
                data = await r.json(content_type=None)
                for item in data.get("hackathons", []):
                    try:
                        raw_themes = item.get("themes", [])
                        tags = [t["name"] if isinstance(t, dict) else str(t) for t in raw_themes]
                        loc  = item.get("displayed_location", {})
                        loc  = loc.get("location", "Online") if isinstance(loc, dict) else str(loc)
                        date = item.get("submission_period_dates", "TBA")
                        events.append(normalize_event(
                            name     = item.get("title", ""),
                            platform = "Devpost",
                            link     = item.get("url", ""),
                            start    = date,
                            end      = date,
                            tags     = tags,
                            location = loc,
                            prize    = str(item.get("prize_amount", "")),
                            cost     = "Free",
                            elig     = "Students/Global",
                        ))
                    except Exception as e:
                        print(f"[Error] {e}")
                        continue
        except Exception as e:
            print(f"[Error] {e}")
    return events


async def _fetch_devpost(session, sem) -> list[dict]:
    page, all_events, seen = 1, [], set()
    while True:
        pages = await asyncio.gather(*[_devpost_page(session, sem, p) for p in range(page, page + 10)])
        new = False
        for pg in pages:
            if not pg:
                return all_events
            for e in pg:
                if e["registrationLink"] and e["registrationLink"] not in seen:
                    seen.add(e["registrationLink"])
                    all_events.append(e)
                    new = True
        if not new:
            break
        page += 10
    return all_events


# ---------------------------------------------------------------------------
# Unstop
# ---------------------------------------------------------------------------

async def _unstop_page(session, sem, page: int) -> list[dict]:
    url     = f"{UNSTOP_API}?opportunity=hackathons&page={page}&per_page=20"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[dict] = []
    async with sem:
        try:
            async with session.get(url, headers=headers) as r:
                if r.status != 200:
                    return events
                data = await r.json(content_type=None)
                for item in data.get("data", {}).get("data", []):
                    try:
                        seo  = item.get("seo_url", "")
                        filters = item.get("filters", [])
                        tags = [f.get("name", "") for f in filters] if isinstance(filters, list) else []
                        events.append(normalize_event(
                            name     = item.get("title", ""),
                            platform = "Unstop",
                            link     = f"https://unstop.com/hackathons/{seo}" if seo else "",
                            start    = item.get("start_date", "TBA"),
                            end      = item.get("end_date", "TBA"),
                            tags     = tags,
                            location = "Virtual/Online",
                            cost     = "Paid" if item.get("payment_type") == "paid" else "Free",
                            elig     = "Students/College",
                        ))
                    except Exception as e:
                        print(f"[Error] {e}")
                        continue
        except Exception as e:
            print(f"[Error] {e}")
    return events


async def _fetch_unstop(session, sem) -> list[dict]:
    page, all_events, seen = 1, [], set()
    while True:
        pages = await asyncio.gather(*[_unstop_page(session, sem, p) for p in range(page, page + 10)])
        new = False
        for pg in pages:
            if not pg:
                return all_events
            for e in pg:
                if e["registrationLink"] and e["registrationLink"] not in seen:
                    seen.add(e["registrationLink"])
                    all_events.append(e)
                    new = True
        if not new:
            break
        page += 10
    return all_events


# ---------------------------------------------------------------------------
# HackerEarth
# ---------------------------------------------------------------------------

async def _hackerearth_page(session, sem, page: int) -> list[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[dict] = []
    async with sem:
        try:
            async with session.get(f"{HACKEREARTH_API}?page={page}", headers=headers) as r:
                if r.status != 200:
                    return events
                data = await r.json(content_type=None)
                for item in data.get("response", []):
                    try:
                        raw_tags = item.get("tags", [])
                        tags = [t["name"] if isinstance(t, dict) else str(t) for t in raw_tags]
                        events.append(normalize_event(
                            name     = item.get("title", ""),
                            platform = "HackerEarth",
                            link     = item.get("url", ""),
                            start    = item.get("start_utc_tz", "TBA"),
                            end      = item.get("end_utc_tz", "TBA"),
                            tags     = tags,
                            location = str(item.get("location", "Online")),
                            cost     = "Free",
                            elig     = "Open",
                        ))
                    except Exception as e:
                        print(f"[Error] {e}")
                        continue
        except Exception as e:
            print(f"[Error] {e}")
    return events


async def _fetch_hackerearth(session, sem) -> list[dict]:
    page, all_events, seen = 1, [], set()
    while True:
        pages = await asyncio.gather(*[_hackerearth_page(session, sem, p) for p in range(page, page + 10)])
        new = False
        for pg in pages:
            if not pg:
                return all_events
            for e in pg:
                if e["registrationLink"] and e["registrationLink"] not in seen:
                    seen.add(e["registrationLink"])
                    all_events.append(e)
                    new = True
        if not new:
            break
        page += 10
    return all_events


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def fetch_all_events() -> list[dict]:
    """
    Scrape all platforms concurrently and return a flat list of raw events.

    No filtering, scoring, or deduplication — that is curate.py's job.

    Returns:
        list[dict]: Combined events from Devfolio, Devpost, Unstop, HackerEarth.
    """
    sem     = asyncio.Semaphore(CONCURRENCY_LIMIT)
    timeout = aiohttp.ClientTimeout(total=SESSION_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(
            _fetch_devfolio(session, sem),
            _fetch_devpost(session, sem),
            _fetch_unstop(session, sem),
            _fetch_hackerearth(session, sem),
            return_exceptions=True,
        )

    events: list[dict] = []
    for res in results:
        if isinstance(res, list):
            events.extend(res)
    return events


async def run_scraper_pipeline() -> dict[str, list[Event]]:
    """
    Run the full scraper pipeline.
    Fetches raw events, cleans them, curates them into IGs, and validates
    them into Pydantic Event objects.
    """
    # 1. Fetch raw events
    print("[Pipeline] Fetching raw events...")
    raw_events = await fetch_all_events()
    print(f"[Pipeline] Fetched {len(raw_events)} raw events.")

    # 2. Clean events (returns primary and extended pools)
    primary, extended = clean_events(raw_events)
    print(f"[Pipeline] Cleaned events: {len(primary)} primary, {len(extended)} extended.")

    # 3. Curate into groups based on IG keywords
    grouped = curate(primary, extended, IG_KEYWORDS)
    curated_count = sum(len(events) for events in grouped.values())
    print(f"[Pipeline] Curated events: {curated_count}.")

    # 4. Convert dictionaries to Pydantic Event objects
    final_output = {}
    seen_links = set()
    total_valid = 0

    for ig in MASTER_IGS:
        final_output[ig] = []
        for e in grouped.get(ig, []):
            # Strict Event Validation
            link = e.get("registrationLink")
            if not link:
                continue
                
            name = e.get("eventName", "")
            if not name or name.strip() == "" or name.lower() in ["unknown", "tba", "n/a"]:
                continue
                
            # Prevent duplicate events across IGs
            if link in seen_links:
                continue
            seen_links.add(link)

            try:
                event_obj = Event(
                    eventName=name.strip(),
                    registrationLink=link.strip(),
                    startDate=e.get("startDate", "TBA"),
                    endDate=e.get("endDate", "TBA"),
                    location=e.get("location"),
                    platform=e.get("platform"),
                    days_remaining=e.get("days_remaining", e.get("_days_remaining")),
                    prizePool=e.get("prizePool"),
                    cost=e.get("cost"),
                    eligibility=e.get("eligibility"),
                    tags=e.get("tags") if isinstance(e.get("tags"), list) else [],
                    score=e.get("score", e.get("_score")),
                    eventType=e.get("eventType", e.get("_event_type")),
                )
                final_output[ig].append(event_obj)
                total_valid += 1
            except Exception as exc:
                print(f"[Pipeline Error] {exc}")
                continue

    print(f"[Pipeline] Curation complete. Validated {total_valid} Pydantic event objects.")
    return final_output


def _print_results(grouped):
    print("\n" + "═" * 55)
    print("  🚀  Scraper Pipeline Data")
    print("═" * 55)

    active_count = 0
    total_events = 0

    for ig, events in grouped.items():
        if not events:
            continue
            
        active_count += 1
        total_events += len(events)
        
        print(f"\n{'─'*55}")
        print(f"  📌  {ig}  ({len(events)} events)")
        print(f"{'─'*55}")
        
        for i, event in enumerate(events, 1):
            if isinstance(event, dict):
                title = event.get('eventName', 'Unknown')
                days  = event.get('days_remaining', 0)
                plat  = event.get('platform', 'Unknown')
                link  = event.get('registrationLink', '')
                loc   = event.get('location', 'Online')
            else:
                title = getattr(event, 'eventName', 'Unknown')
                days  = getattr(event, 'days_remaining', 0)
                plat  = getattr(event, 'platform', 'Unknown')
                link  = getattr(event, 'registrationLink', '')
                loc   = getattr(event, 'location', 'Online')
            
            print(f"\n  {i}. {title}")
            print(f"     {days}d left │ {plat} │ {loc}")
            print(f"     🔗 {link}")

    print(f"\n{'═'*55}")
    print(f"  ✅  {active_count} IGs active  │  {total_events} events")
    print(f"{'═'*55}\n")

def save_events(grouped: dict):
    db_obj = DatabaseFacade()
    inserted, modified = 0, 0
    for ig, events_list in grouped.items():
        for event in events_list:
            if hasattr(event, 'model_dump'): event = event.model_dump()
            elif hasattr(event, 'dict'): event = event.dict()
            elif not isinstance(event, dict): event = vars(event)
            
            title = event.get('eventName', 'Unknown')
            link = event.get('registrationLink', '')
            if not link: continue
            
            if not db_obj.link_exists(link, ig):
                doc_id = db_obj.insert_event(title, link, ig, "API", "not processed")
                if doc_id: inserted += 1
            else: 
                modified += 1
    return inserted, modified

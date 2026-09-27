"""
src/scraping/data_cleaner.py
============================
Strict pre-processing layer for the scraper pipeline.

Rules:
    1. Unstop URL deduplication/repair
    2. Location filter (Online/Virtual + Kerala offline only)
    3. Strict date window (0–10 days only)
    4. University-specific exclusion
"""

import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Location whitelist (Online + Kerala ONLY for offline)
# ---------------------------------------------------------------------------

LOCATION_ALLOW_ONLINE: list[str] = [
    "online", "virtual", "remote", "anywhere", "web"
]

LOCATION_ALLOW_KERALA: list[str] = [
    "kerala", "kochi", "trivandrum", "ernakulam", "calicut", "thrissur",
    "kozhikode", "malappuram", "kannur", "kasargod", "alappuzha",
    "kollam", "idukki", "pathanamthitta", "wayanad", "palai",
]

# University-specific keywords
UNIVERSITY_SPECIFIC_KEYWORDS: list[str] = [
    "senior design", "capstone", "internal hackathon", "student-only",
    "class project", "course project", "final year project",
    "semester project", "thesis", "dissertation",
    "lab assignment", "department of", "faculty of",
    "university challenge",
]

# Strict window to 10 days
MAX_EVENT_WINDOW_DAYS: int = 10

# Date parsing helpers
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

# ═══════════════════════════════════════════════════════════════════════════
# Rule 1 — Unstop URL Repair
# ═══════════════════════════════════════════════════════════════════════════

_UNSTOP_BASE = "https://unstop.com/hackathons/"

def clean_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return url
    url = url.strip()
    count = url.count(_UNSTOP_BASE)
    if count > 1:
        last_idx = url.rfind(_UNSTOP_BASE)
        url = url[last_idx:]
    double_scheme = re.search(r'(https?://.*?)(https?://)', url)
    if double_scheme:
        url = url[double_scheme.start(2):]
    return url

# ═══════════════════════════════════════════════════════════════════════════
# Rule 2 — Smart Location Filter
# ═══════════════════════════════════════════════════════════════════════════

def is_location_allowed(location: str) -> bool:
    """
    ALLOW:
        - Online / Virtual / Remote
        - Kerala offline events
        - Empty/TBA locations
    REJECT:
        - Offline events outside Kerala
    """
    if not location or not isinstance(location, str):
        return True

    loc = location.strip().lower()
    if not loc or loc in {"tba", "n/a", "none", "unknown", ""}:
        return True

    if any(kw in loc for kw in LOCATION_ALLOW_ONLINE):
        return True

    if any(kw in loc for kw in LOCATION_ALLOW_KERALA):
        return True

    # If it's not online and not in Kerala, reject
    return False

# ═══════════════════════════════════════════════════════════════════════════
# Rule 3 — Date Window (0–30 days)
# ═══════════════════════════════════════════════════════════════════════════

def _parse_date(date_str: str) -> datetime | None:
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip().lower()
    if s in {"tba", "live", "none", "n/a", "ongoing", ""}:
        return None

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    target = date_str.split("-")[-1].lower()
    year_m = re.search(r"(\d{4})", date_str)
    year = int(year_m.group(1)) if year_m else datetime.now().year
    day_m = re.search(r"(\d{1,2})", target)
    if day_m:
        day = int(day_m.group(1))
        month = next((v for k, v in _MONTHS.items() if k in target), None) \
             or next((v for k, v in _MONTHS.items() if k in s), None)
        if month:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
    return None

def get_days_remaining(event: dict) -> int | None:
    now = datetime.now()
    for field in ["endDate", "startDate"]:
        parsed = _parse_date(event.get(field, "TBA"))
        if parsed is not None:
            return (parsed - now).days
    return None

def is_valid_date(event: dict) -> bool:
    """
    NEVER allow past events.
    Keep 0-10 days.
    Reject TBA entirely.
    """
    days = get_days_remaining(event)
    if days is None:
        return False
    
    return 0 <= days <= MAX_EVENT_WINDOW_DAYS

# ═══════════════════════════════════════════════════════════════════════════
# Rule 4 — University-Specific Exclusion
# ═══════════════════════════════════════════════════════════════════════════

def is_university_specific(event: dict) -> bool:
    blob = " ".join([
        str(event.get("eventName", "")),
        str(event.get("description", ""))[:300],
        str(event.get("eligibility", "")),
        " ".join(str(t) for t in event.get("tags", [])),
    ]).lower()
    return any(kw in blob for kw in UNIVERSITY_SPECIFIC_KEYWORDS)

# ═══════════════════════════════════════════════════════════════════════════
# Rule 5 — Vague Title Exclusion
# ═══════════════════════════════════════════════════════════════════════════

def is_quality_event(event: dict) -> bool:
    title = str(event.get("eventName", "")).lower()
    if not title: return False
    
    # Reject explicitly vague or demo items
    vague_patterns = ["test ", " test", "demo ", " demo", "dummy", "untitled"]
    
    if any(kw in title for kw in vague_patterns): 
        return False
        
    return True

# ═══════════════════════════════════════════════════════════════════════════
# Master cleaning function
# ═══════════════════════════════════════════════════════════════════════════

def clean_events(events: list[dict], use_ai: bool = False):
    primary = []
    extended = []

    for event in events:
        # --- reject bad titles ---
        title = event.get("eventName", "").lower().strip()
        if not title or title in {"unknown", "tba", "n/a", "none"}:
            continue
        # --- reject bad titles (word-boundary match: allows 'contest', 'latest', 'testnet') ---
        BAD_TITLE_PATTERN = re.compile(r"\b(?:test|demo|untitled|sample|dummy)\b", re.IGNORECASE)
        if BAD_TITLE_PATTERN.search(title):
            # Check if this is a known test fixture in test suite or dummy entry
            # Still reject placeholder names like 'demo', 'test event', 'untitled'
            continue

        # --- reject missing link ---
        link = str(event.get("registrationLink", "")).strip()
        if not link:
            continue

        # --- repair url ---
        fixed_link = clean_url(link)
        if fixed_link != link:
            event["registrationLink"] = fixed_link
            link = fixed_link

        # --- ROUTING: News bypasses strict location/date filtering ---
        if event.get("_event_type") == "News":
            primary.append(event)
            continue

        # --- location filter ---
        if not is_location_allowed(event.get("location", "")):
            continue

        # --- university filter ---
        if is_university_specific(event):
            continue

        # --- parse date ---
        days = get_days_remaining(event)
        if days is None:
            continue

        # --- reject past events always ---
        if days < 0:
            continue

        # --- reject beyond 10 days always ---
        if days > MAX_EVENT_WINDOW_DAYS:
            continue

        event["days_remaining"] = days
        event["_days_remaining"] = days

        # --- route to correct pool ---
        primary.append(event)

    print(f"[DataCleaner] primary={len(primary)}  extended={len(extended)}")
    return primary, extended

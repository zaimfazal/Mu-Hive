from __future__ import annotations

import logging
from src.db.postgres_database import DatabaseFacade
from src.db.connection import db_conn
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


DEFAULT_QUALITY_SCORE = 7
IG_NORMALIZATION = {
    # "Ai" and "Ui Ux" are no longer needed — constants.py now uses "AI" and "UI/UX" directly.
}


def _to_plain_event(event: object) -> dict:
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    if isinstance(event, dict):
        return event
    return vars(event)





def _normalize_ig(ig: str) -> str:
    return IG_NORMALIZATION.get(ig, ig)


def _validity_from_curated_score(score: int | None) -> int:
    """Map scraper curation points onto the 1-10 event validity scale."""
    if score is None:
        return DEFAULT_QUALITY_SCORE
    try:
        value = int(score)
    except (TypeError, ValueError):
        return DEFAULT_QUALITY_SCORE
    if value >= 230:
        return 10
    if value >= 190:
        return 9
    if value >= 150:
        return 8
    if value >= 110:
        return 7
    if value >= 70:
        return 6
    return 5


_MISSING_SUMMARY_TOKENS = {"", "n/a", "na", "tba", "unknown", "none", "null"}


def _is_summary_value(value: object) -> bool:
    """True when a metadata value is worth rendering (not blank/placeholder)."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in _MISSING_SUMMARY_TOKENS


def _fallback_summary(
    title: str,
    ig: str,
    platform: str | None = None,
    location: str | None = None,
    days_left: int | None = None,
    description: str | None = None,
    deadline: str | None = None,
    prize: str | None = None,
) -> str:
    """Build a deterministic hackathon summary from structured metadata.

    Omits any field that is missing or a placeholder instead of rendering
    "None", "Unknown", or empty labels.
    """
    has_title = _is_summary_value(title)
    has_ig = _is_summary_value(ig)
    clean_title = str(title).strip() if has_title else ""
    clean_ig = str(ig).strip() if has_ig else ""

    if clean_title and clean_ig:
        head = f"{clean_title} is a {clean_ig}-relevant hackathon"
    elif clean_title:
        head = f"{clean_title} is a hackathon"
    elif clean_ig:
        head = f"A {clean_ig}-relevant hackathon"
    else:
        head = "A hackathon"

    if _is_summary_value(platform):
        head += f" on {str(platform).strip()}"
    if _is_summary_value(location):
        head += f" hosted {str(location).strip()}"
    if days_left is not None:
        try:
            days = int(days_left)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            days = None
        if days is not None and days >= 0:
            head += f" with {days} day(s) left"
    if _is_summary_value(deadline):
        head += f", deadline {str(deadline).strip()}"
    if _is_summary_value(prize):
        head += f", prize {str(prize).strip()}"
    head += "."

    if _is_summary_value(description):
        desc = " ".join(str(description).split())
        if len(desc) > 200:
            desc = desc[:197].rsplit(" ", 1)[0] + "..."
        head += f" {desc}"

    return head


def _ensure_events_schema() -> None:
    with db_conn.get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                title TEXT,
                ig TEXT,
                category TEXT,
                summary TEXT,
                apply_link TEXT UNIQUE,
                validity_score INTEGER,
                platform TEXT,
                location TEXT,
                days_left INTEGER,
                mail_sent BOOLEAN DEFAULT FALSE,
                zulip_sent BOOLEAN DEFAULT FALSE,
                deadline TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS title TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS ig TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS category TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS summary TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS apply_link TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS validity_score INTEGER;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS platform TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS location TEXT;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS days_left INTEGER;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS mail_sent BOOLEAN DEFAULT FALSE;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS zulip_sent BOOLEAN DEFAULT FALSE;")
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS deadline TEXT;")
        cur.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
        )
        cur.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
        )
        # Ensure UNIQUE constraint exists even if table was created without it
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'events_apply_link_key'
                ) THEN
                    ALTER TABLE events ADD CONSTRAINT events_apply_link_key UNIQUE (apply_link);
                END IF;
            END $$;
        """)
        # Junction table safety net (canonical DDL + backfill live in schema.py).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS event_interest_groups (
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                ig_name TEXT NOT NULL,
                PRIMARY KEY (event_id, ig_name)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_igs_name
            ON event_interest_groups (ig_name);
        """)


def _upsert_event_without_constraint(
    *,
    title: str,
    ig: str,
    summary: str | None,
    apply_link: str,
    validity_score: int,
    platform: str | None = None,
    location: str | None = None,
    days_left: int | None = None,
    deadline: str | None = None,
) -> int:
    """Upsert one canonical event row by apply_link (race-safe).

    Delegates to EventRepository.upsert, whose single INSERT ... ON CONFLICT
    (apply_link) statement is atomic under concurrent writers (the previous
    SELECT-then-INSERT lost races with UniqueViolation). Junction memberships
    for [ig] are synced in the same cursor. mail_sent/zulip_sent untouched.
    """
    from src.db.repositories.event_repository import EventRepository

    return EventRepository.upsert({
        "title": title,
        "ig": ig,
        "ig_names": [ig],
        "category": "Hackathons",
        "summary": summary,
        "apply_link": apply_link,
        "validity_score": validity_score,
        "platform": platform,
        "location": location,
        "days_left": days_left,
        "deadline": deadline,
    })


async def save_orchestrator_events(grouped_events: dict[str, list]) -> dict[str, int]:
    """
    Persist orchestrator hackathon output into the same Postgres DB used by
    Zulip and Gmail sender scripts.

    Hackathons from the API already have structured metadata, so we generate
    summaries directly here (no scraping needed) and mark them as processed.
    """
    db = DatabaseFacade()
    _ensure_events_schema()
    inserted_scraped = 0
    upserted_events = 0

    from src.utils.ig_normalizer import normalize_ig

    for ig, events in (grouped_events or {}).items():
        normalized_ig = normalize_ig(ig)
        if normalized_ig is None or normalized_ig.strip().lower() == "unknown":
            logger.warning(f"Skipping group: invalid IG '{ig}'")
            continue
        for raw_event in events:
            event = _to_plain_event(raw_event)
            title = str(event.get("eventName", "Unknown")).strip()
            link = str(event.get("registrationLink", "")).strip()
            if not link:
                continue

            # Extract metadata into dedicated columns
            platform = event.get("platform", None)
            location = event.get("location", None)
            days_left = event.get("days_remaining", None)
            end_date = event.get("endDate", None)
            start_date_fallback = event.get("startDate", None)
            deadline = end_date or start_date_fallback  # Use endDate as deadline, fallback to startDate
            source_engine = platform or "API"
            validity_score = _validity_from_curated_score(event.get("score"))
            structured_metadata = {
                "Platform": platform or source_engine,
                "Start": start_date_fallback or "TBA",
                "End": end_date or "TBA",
                "Location": location or "Online",
                "Mode": "Online" if str(location or "").lower() in {"", "online", "virtual/online"} else location,
                "Prize Pool": event.get("prizePool") or "",
                "Registration Link": link,
                "Cost": event.get("cost") or "",
                "Eligibility": event.get("eligibility") or "",
                "Tags": ", ".join(event.get("tags", []) or []),
            }

            # ── Deterministic summary from structured API metadata (no LLM call) ──
            summary = _fallback_summary(
                title,
                normalized_ig,
                platform,
                location,
                days_left,
                description=event.get("description"),
                deadline=deadline,
                prize=event.get("prizePool"),
            )
            try:
                inserted = db.insert_opportunity(
                    title=title,
                    link=link,
                    summary=summary,
                    source_engine=source_engine,
                    ig_tags=[normalized_ig],
                    category="Hackathons",
                    is_processed=True,
                    quality_score=validity_score,
                    metadata={
                        "platform": platform,
                        "location": location,
                        "days_left": days_left,
                        "startDate": start_date_fallback,
                        "endDate": end_date,
                        "deadline": deadline,
                        "structured_metadata": structured_metadata,
                        "raw_curated_score": event.get("score"),
                        "eventType": event.get("eventType"),
                        "prizePool": event.get("prizePool"),
                        "cost": event.get("cost"),
                        "eligibility": event.get("eligibility"),
                        "tags": event.get("tags", []),
                    },
                )
                _upsert_event_without_constraint(
                    title=title,
                    ig=normalized_ig,
                    summary=summary,
                    apply_link=link,
                    validity_score=validity_score,
                    platform=platform,
                    location=location,
                    days_left=days_left,
                    deadline=deadline,

                )
                if inserted:
                    inserted_scraped += 1
                upserted_events += 1
            except Exception as e:
                logger.error(f"Failed to save hackathon '{title}': {e}. Rolling back.")
                with db_conn.get_cursor() as rollback_cur:
                    rollback_cur.execute(
                        "UPDATE scraped_data SET status = 'not processed' WHERE url = %s AND status = 'processed'",
                        (link,)
                    )

    db.close()
    return {
        "inserted_scraped": inserted_scraped,
        "upserted_events": upserted_events,
    }

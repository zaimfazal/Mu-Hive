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
    now = datetime.now(timezone.utc)
    with db_conn.get_cursor() as cur:
        cur.execute(
            "SELECT id FROM events WHERE apply_link = %s ORDER BY id DESC LIMIT 1;",
            (apply_link,),
        )
        existing = cur.fetchone()

        if existing:
            cur.execute(
                """
                UPDATE events
                SET title = %s,
                    ig = %s,
                    category = %s,
                    summary = %s,
                    validity_score = %s,
                    platform = %s,
                    location = %s,
                    days_left = %s,
                    deadline = %s,
                    updated_at = %s
                WHERE id = %s;
                """,
                (title, ig, "Hackathons", summary, validity_score, platform, location, days_left, deadline, now, existing[0]),
            )
            return existing[0]

        cur.execute(
            """
            INSERT INTO events (
                title, ig, category, summary, apply_link, validity_score,
                platform, location, days_left, deadline, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (title, ig, "Hackathons", summary, apply_link, validity_score, platform, location, days_left, deadline, now),
        )
        return cur.fetchone()[0]


async def save_orchestrator_events(grouped_events: dict[str, list]) -> dict[str, int]:
    """
    Persist orchestrator hackathon output into the same Postgres DB used by
    Zulip and Gmail sender scripts.

    Hackathons from the API already have structured metadata, so we generate
    summaries directly here (no scraping needed) and mark them as processed.
    """
    import asyncio
    from src.agents.summarizer import summarizer_agent

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

            # ── Generate summary from structured API metadata ──
            summary = None
            try:
                summary_prompt = (
                    f"Title: {title}\n"
                    f"Category: Hackathons\n"
                    f"Interest Group: {normalized_ig}\n"
                    f"Platform: {platform or 'N/A'}\n"
                    f"Location: {location or 'Online'}\n"
                    f"Start Date: {start_date_fallback or 'TBA'}\n"
                    f"End Date: {end_date or 'TBA'}\n"
                    f"Days Left: {days_left or 'N/A'}\n\n"
                    "Write a crisp 1-sentence summary."
                )
                result = await summarizer_agent.run(summary_prompt)
                summary = result.output.summary
            except Exception as e:
                logger.warning(f"Summarizer failed for '{title}': {e}")

            try:
                inserted = db.insert_opportunity(
                    title=title,
                    link=link,
                    summary=summary,
                    source_engine=source_engine,
                    ig_tags=[normalized_ig],
                    category="Hackathons",
                    is_processed=True,
                    quality_score=DEFAULT_QUALITY_SCORE,
                )
                _upsert_event_without_constraint(
                    title=title,
                    ig=normalized_ig,
                    summary=summary,
                    apply_link=link,
                    validity_score=DEFAULT_QUALITY_SCORE,
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

            # Throttle to avoid rate limits
            if summary:
                await asyncio.sleep(1)

    db.close()
    return {
        "inserted_scraped": inserted_scraped,
        "upserted_events": upserted_events,
    }

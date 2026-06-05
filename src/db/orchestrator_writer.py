from __future__ import annotations

from src.db.postgres_database import DatabaseFacade
from src.db.connection import db_conn
from datetime import datetime, timezone


DEFAULT_QUALITY_SCORE = 7
IG_NORMALIZATION = {
    "Ai": "AI",
    "Ui Ux": "UI/UX",
}


def _to_plain_event(event: object) -> dict:
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    if isinstance(event, dict):
        return event
    return vars(event)


def _build_hackathon_summary(event: dict) -> str:
    """Build a structured Key: Value summary with all available metadata."""
    lines = [
        f"Platform: {event.get('platform', 'Unknown')}",
        f"Start: {event.get('startDate', 'TBA')}",
        f"End: {event.get('endDate', 'TBA')}",
        f"Location: {event.get('location', 'Online')}",
    ]
    prize = event.get('prizePool', '')
    if prize and prize not in ('', 'TBA'):
        lines.append(f"Prize Pool: {prize}")
    cost = event.get('cost', '')
    if cost:
        lines.append(f"Cost: {cost}")
    elig = event.get('eligibility', '')
    if elig:
        lines.append(f"Eligibility: {elig}")
    tags = event.get('tags', [])
    if tags and isinstance(tags, list):
        lines.append(f"Tags: {', '.join(str(t) for t in tags)}")
    days = event.get('days_remaining')
    if days is not None:
        lines.append(f"Days Remaining: {days}")
    return "\n".join(lines)


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
                apply_link TEXT,
                validity_score INTEGER,
                mail_sent BOOLEAN DEFAULT FALSE,
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
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS mail_sent BOOLEAN DEFAULT FALSE;")
        cur.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
        )
        cur.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
        )


def _upsert_event_without_constraint(
    *,
    title: str,
    ig: str,
    summary: str,
    apply_link: str,
    validity_score: int,
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
                    updated_at = %s
                WHERE id = %s;
                """,
                (title, ig, "Hackathons", summary, validity_score, now, existing[0]),
            )
            return existing[0]

        cur.execute(
            """
            INSERT INTO events (
                title, ig, category, summary, apply_link, validity_score, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (title, ig, "Hackathons", summary, apply_link, validity_score, now),
        )
        return cur.fetchone()[0]


def save_orchestrator_events(grouped_events: dict[str, list]) -> dict[str, int]:
    """
    Persist orchestrator hackathon output into the same Postgres DB used by
    Zulip and Gmail sender scripts.
    """
    db = DatabaseFacade()
    _ensure_events_schema()
    inserted_scraped = 0
    upserted_events = 0

    for ig, events in (grouped_events or {}).items():
        normalized_ig = _normalize_ig(ig)
        for raw_event in events:
            event = _to_plain_event(raw_event)
            title = str(event.get("eventName", "Unknown")).strip()
            link = str(event.get("registrationLink", "")).strip()
            if not link:
                continue

            summary = _build_hackathon_summary(event)
            source_engine = event.get("platform", "API")

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
            if inserted:
                inserted_scraped += 1

            _upsert_event_without_constraint(
                title=title,
                ig=normalized_ig,
                summary=summary,
                apply_link=link,
                validity_score=DEFAULT_QUALITY_SCORE,
            )
            upserted_events += 1

    db.close()
    return {
        "inserted_scraped": inserted_scraped,
        "upserted_events": upserted_events,
    }

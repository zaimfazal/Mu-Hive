from datetime import datetime, timezone
from src.db.connection import db_conn

class EventRepository:
    """Handles all database operations related to structured events."""

    @staticmethod
    def clean_ig_names(ig_names):
        """Deduplicate IG names, dropping blanks/placeholders. Order-preserving."""
        cleaned = []
        seen = set()
        for name in ig_names or []:
            if name is None:
                continue
            text = str(name).strip()
            if not text or text.lower() == "unknown":
                continue
            if text not in seen:
                seen.add(text)
                cleaned.append(text)
        return cleaned

    @staticmethod
    def sync_interest_groups(cur, event_id, ig_names):
        """Replace an event's junction memberships with ig_names (idempotent).

        Must be called with the same cursor used for the event write where
        possible. INSERTs use ON CONFLICT DO NOTHING so repeated ingestion
        never creates duplicate relationships.
        """
        cleaned = EventRepository.clean_ig_names(ig_names)
        if cleaned:
            cur.execute(
                "DELETE FROM event_interest_groups "
                "WHERE event_id = %s AND ig_name NOT IN %s;",
                (event_id, tuple(cleaned)),
            )
            for ig_name in cleaned:
                cur.execute(
                    "INSERT INTO event_interest_groups (event_id, ig_name) "
                    "VALUES (%s, %s) "
                    "ON CONFLICT (event_id, ig_name) DO NOTHING;",
                    (event_id, ig_name),
                )
        else:
            cur.execute(
                "DELETE FROM event_interest_groups WHERE event_id = %s;",
                (event_id,),
            )

    @staticmethod
    def upsert(event_data):
        """
        Upserts an event based on the apply_link.
        Updates all fields and updated_at on conflict.
        """
        with db_conn.get_cursor() as cur:
            cur.execute("""
                INSERT INTO events (
                    title, ig, category, summary, apply_link, validity_score,
                    platform, location, days_left, deadline, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (apply_link) DO UPDATE SET
                    title = EXCLUDED.title,
                    ig = EXCLUDED.ig,
                    category = EXCLUDED.category,
                    summary = EXCLUDED.summary,
                    validity_score = EXCLUDED.validity_score,
                    platform = EXCLUDED.platform,
                    location = EXCLUDED.location,
                    days_left = EXCLUDED.days_left,
                    deadline = EXCLUDED.deadline,
                    updated_at = EXCLUDED.updated_at
                RETURNING id;
            """, (
                event_data.get('title'),
                event_data.get('ig'),
                event_data.get('category'),
                event_data.get('summary'),
                event_data.get('apply_link', event_data.get('link')),
                event_data.get('validity_score', event_data.get('score')),
                event_data.get('platform'),
                event_data.get('location'),
                event_data.get('days_left'),
                event_data.get('deadline'),
                datetime.now(timezone.utc)
            ))
            event_id = cur.fetchone()[0]
            # Maintain all validated memberships; legacy events.ig keeps tags[0].
            ig_names = event_data.get('ig_names')
            if ig_names is None:
                ig_names = [event_data.get('ig')]
            EventRepository.sync_interest_groups(cur, event_id, ig_names)
            return event_id

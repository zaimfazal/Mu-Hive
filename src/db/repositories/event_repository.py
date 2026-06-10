from datetime import datetime, timezone
from src.db.connection import db_conn

class EventRepository:
    """Handles all database operations related to structured events."""

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
            return cur.fetchone()[0]

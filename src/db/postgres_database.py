import json
from datetime import datetime, timedelta, timezone
from psycopg2.extras import RealDictCursor
from src.db.connection import db_conn
from src.db.schema import initialize_schema
from src.db.repositories.scrape_repository import ScrapeRepository
from src.db.repositories.event_repository import EventRepository
from src.config.settings import (
    ORCHESTRATOR_SINCE_DAYS,
    ORCHESTRATOR_SINCE_DATE,
    ORCHESTRATOR_UNTIL_DATE,
)

class DatabaseFacade:
    """
    A unified interface for the database system.
    This class coordinates schema initialization and delegates work to specific repositories.
    """
    def __init__(self):
        # Automatically initialize schema on startup
        initialize_schema()
        self.scrapes = ScrapeRepository()
        self.events = EventRepository()
        self._orchestrator_since, self._orchestrator_until = self._resolve_orchestrator_window()

    @staticmethod
    def _parse_iso_datetime(value, env_name, end_of_day=False):
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid {env_name}: {value!r}. Use an ISO date or datetime."
            ) from exc

        if "T" not in raw and " " not in raw:
            parsed = parsed.replace(
                hour=23 if end_of_day else 0,
                minute=59 if end_of_day else 0,
                second=59 if end_of_day else 0,
                microsecond=999999 if end_of_day else 0,
            )

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _resolve_orchestrator_window(self):
        since = None
        until = None

        if ORCHESTRATOR_SINCE_DAYS:
            try:
                days = int(ORCHESTRATOR_SINCE_DAYS)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid ORCHESTRATOR_SINCE_DAYS: {ORCHESTRATOR_SINCE_DAYS!r}. "
                    "Use a non-negative integer."
                ) from exc
            if days < 0:
                raise ValueError("ORCHESTRATOR_SINCE_DAYS must be >= 0.")
            since = datetime.now(timezone.utc) - timedelta(days=days)

        if ORCHESTRATOR_SINCE_DATE:
            since = self._parse_iso_datetime(
                ORCHESTRATOR_SINCE_DATE, "ORCHESTRATOR_SINCE_DATE", end_of_day=False
            )

        if ORCHESTRATOR_UNTIL_DATE:
            until = self._parse_iso_datetime(
                ORCHESTRATOR_UNTIL_DATE, "ORCHESTRATOR_UNTIL_DATE", end_of_day=True
            )

        if since and until and since > until:
            raise ValueError(
                "Invalid orchestrator date range: "
                "ORCHESTRATOR_SINCE_DATE/SINCE_DAYS is after ORCHESTRATOR_UNTIL_DATE."
            )

        return since, until

    def _append_orchestrator_time_filter(self, query, params, column="scraped_at"):
        if self._orchestrator_since is not None:
            query += f" AND {column} >= %s"
            params.append(self._orchestrator_since)
        if self._orchestrator_until is not None:
            query += f" AND {column} <= %s"
            params.append(self._orchestrator_until)
        return query, params

    # Backward compatibility methods for existing code
    def save_scrape_result(self, url, data, title=None, ig=None, status="not processed", scraped_at=None):
        """Flexible save method for orchestrator and other scripts."""
        if title is None:
            # Try to extract title from data if not provided
            title = data.get('scraped_page_title') or data.get('title', 'Unknown Source')
            
        return self.scrapes.save(title, url, ig, status, data, scraped_at)

    def get_scrape_result(self, record_id):
        return self.scrapes.get_by_id(record_id)

    def get_all_results(self):
        return self.scrapes.get_all()

    def upsert_event(self, event_data):
        return self.events.upsert(event_data)

    # ----------------------------------------------------
    # MongoDB Backwards Compatibility Wrappers
    # ----------------------------------------------------
    def link_exists(self, link, keyword):
        return self.scrapes.link_exists(link, keyword)
        
    def insert_event(self, title, link, keyword_used, source_engine, status):
        return self.scrapes.insert_queue(title, link, keyword_used, source_engine, status)
        
    def find_pending_scrape(self, limit=50):
        rows = self.scrapes.find_pending(limit=limit)
        # Adapt keys for the scraper scripts expecting MongoDB dicts
        for row in rows:
            row['_id'] = row['id']
            row['link'] = row['url']
        return rows
        
    def update_event_scrape(self, doc_id, status, scraped_page_title=None, scraped_meta_description=None, scraped_full_text=None, scrape_layer=None, scrape_error=None, retry_count=None):
        scrape_data = {}
        if scraped_page_title: scrape_data['scraped_page_title'] = scraped_page_title
        if scraped_meta_description: scrape_data['scraped_meta_description'] = scraped_meta_description
        if scraped_full_text: scrape_data['scraped_full_text'] = scraped_full_text
        if scrape_error: scrape_data['scrape_error'] = scrape_error
        if retry_count is not None: scrape_data['retry_count'] = retry_count
        
        return self.scrapes.update_scrape(doc_id, status, scrape_layer, scrape_data)

    # ----------------------------------------------------
    # New Methods for Scout and Intelligence Agents
    # ----------------------------------------------------
    def insert_opportunity(
        self,
        title,
        link,
        summary=None,
        source_engine=None,
        ig_tags=None,
        category=None,
        is_processed=False,
        quality_score=None,
        metadata=None,
    ):
        """Bridge method for scout.py"""
        if self.scrapes.link_exists(link, ig_tags[0] if ig_tags else None):
            return False
            
        data = {
            "summary": summary,
            "ig_tags": ig_tags,
            "category": category,
            "quality_score": quality_score
        }
        if metadata:
            data.update(metadata)
        status = "processed" if is_processed else "not processed"
        
        # Mapping ig_tags to the 'ig' column (primary IG)
        primary_ig = ig_tags[0] if ig_tags else "Unknown"
        
        return self.scrapes.insert_queue(title, link, primary_ig, source_engine, status, data)

    def get_unprocessed_for_intelligence(self, limit=15):
        """Fetch items that are scraped but not yet evaluated by LLM."""
        query = """
            SELECT * FROM scraped_data
            WHERE status = 'scraped'
        """
        params = []
        query, params = self._append_orchestrator_time_filter(query, params, column="scraped_at")
        query += """
            ORDER BY
                CASE WHEN source = 'RSS' THEN 0 ELSE 1 END,
                scraped_at DESC
            LIMIT %s;
        """
        params.append(limit)

        with db_conn.get_cursor(factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            
        # Adapt for MongoDB-style access in intelligence.py
        for row in rows:
            row['_id'] = row['id']
            row['link'] = row['url']
            # Flatten some fields from JSONB for easier access
            json_data = row.get('data') or {}
            row['summary'] = json_data.get('summary')
            row['ig_tags'] = json_data.get('ig_tags', [])
            row['category'] = json_data.get('category', 'Unknown')
            # Extract scraped text from data
            row['scraped_full_text'] = json_data.get('scraped_full_text')
            
        return rows

    @staticmethod
    def _normalize_category(category):
        normalized = str(category or "").strip().lower()
        if normalized == "hackathons":
            return "Hackathons"
        if normalized == "news":
            return "News"
        return "News"

    def update_intelligence(self, item_id, score, tags, generated_summary=None, category=None, raw_score=None, score_breakdown=None, structured_metadata=None):
        """Update item after LLM evaluation."""
        with db_conn.get_cursor() as cur:
            # Fetch current data to preserve other fields
            cur.execute("SELECT title, url, source, data FROM scraped_data WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return False
            title, url, source, data = row[0], row[1], row[2], (row[3] if row[3] else {})
            
            data['quality_score'] = score
            data['validated_tags'] = tags
            if raw_score is not None:
                data['raw_quality_score'] = raw_score
            if score_breakdown is not None:
                data['score_breakdown'] = score_breakdown
            
            # Store LLM-generated summary if available (Bug 7 fix)
            if generated_summary:
                data['summary'] = generated_summary
            
            # Extract final summary for the events table (use LLM summary or fallback to original scraped summary)
            final_summary = generated_summary or data.get('summary') or ''
            
            normalized_category = self._normalize_category(category or data.get('category'))
            data['category'] = normalized_category

            # Merge structured metadata from LLM (hackathon details)
            if structured_metadata:
                data['structured_metadata'] = structured_metadata
            
            status = 'processed' if score > 0 else 'irrelevant'
            
            try:
                cur.execute("""
                    UPDATE scraped_data 
                    SET status = %s, data = %s, ig = %s
                    WHERE id = %s
                """, (status, json.dumps(data), tags[0] if tags else 'Unknown', item_id))
            except Exception as e:
                # Catch unique violation if the URL and IG combination already exists
                import psycopg2
                if isinstance(e, psycopg2.errors.UniqueViolation) or "unique constraint" in str(e).lower():
                    import logging
                    logging.getLogger(__name__).warning(
                        "Duplicate scraped_data entry found for URL: %s and IG: %s. Merging into existing row and removing duplicate ID %s.",
                        url, tags[0] if tags else 'Unknown', item_id
                    )
                    # Merge: update the existing row with the new score/tags, then delete the duplicate
                    new_ig = tags[0] if tags else 'Unknown'
                    cur.execute(
                        """UPDATE scraped_data
                           SET status = %s, data = %s
                           WHERE url = %s AND ig = %s AND id != %s""",
                        (status, json.dumps(data), url, new_ig, item_id)
                    )
                    cur.execute("DELETE FROM scraped_data WHERE id = %s", (item_id,))
                    return False
                raise e
            
            if status == 'processed' and tags:
                # Extract optional fields from scraped_data
                platform_val = data.get('platform') or source or 'RSS'
                location_val = data.get('location')

                # Extract deadline from structured_metadata (LLM returns 'End' key)
                deadline_val = None
                if structured_metadata:
                    deadline_val = (
                        structured_metadata.get('End')
                        or structured_metadata.get('end')
                        or structured_metadata.get('Deadline')
                        or structured_metadata.get('deadline')
                    )
                # Fallback to JSONB data fields
                if not deadline_val:
                    deadline_val = data.get('endDate') or data.get('deadline')

                # Extract location from structured_metadata if not already set
                if not location_val and structured_metadata:
                    location_val = (
                        structured_metadata.get('Location')
                        or structured_metadata.get('location')
                        or structured_metadata.get('Mode')
                        or structured_metadata.get('mode')
                    )

                # Extract platform from structured_metadata if not already set
                if structured_metadata and (not platform_val or platform_val == 'RSS'):
                    platform_val = (
                        structured_metadata.get('Platform')
                        or structured_metadata.get('platform')
                        or platform_val
                    )

                # Compute days_left / deadline if possible
                days_left_val = None
                if 'days_left' in data:
                    days_left_val = data.get('days_left')
                else:
                    # Try to compute from deadline if present (ISO format)
                    deadline_str = deadline_val or data.get('endDate') or data.get('startDate')
                    if deadline_str:
                        try:
                            from datetime import datetime, timezone
                            dl_dt = datetime.fromisoformat(deadline_str.replace('Z', '+00:00')).astimezone(timezone.utc)
                            now = datetime.now(timezone.utc)
                            delta = (dl_dt - now).days
                            days_left_val = max(delta, 0)
                        except Exception:
                            days_left_val = None
                # Skip if event already exists (apply_link unique)
                cur.execute("SELECT id FROM events WHERE apply_link = %s", (url,))
                if not cur.fetchone():
                    try:
                        self.events.upsert({
                            'title': title,
                            'ig': tags[0],
                            'category': normalized_category,
                            'summary': final_summary,
                            'apply_link': url,
                            'validity_score': score,
                            'platform': platform_val,
                            'location': location_val,
                            'days_left': days_left_val,
                            'deadline': deadline_val,
                        })
                    except Exception as evt_err:
                        import logging
                        logging.getLogger(__name__).error(
                            "Events upsert failed for %s: %s", url, evt_err
                        )
            
            return cur.rowcount > 0

    def get_top_opportunities_by_ig_and_category(self, ig, category, limit=5, include_sent=True):
        """Fetches top-scored opportunities for a specific IG and Category from the events table.
        Uses DISTINCT ON to prevent duplicate events when scraped_data has multiple rows for the same URL."""
        inner_query = """
            SELECT DISTINCT ON (e.apply_link)
                e.id, e.title, e.apply_link, e.ig, e.category, e.summary,
                e.validity_score, e.platform, e.location, e.deadline,
                e.days_left, e.zulip_sent, e.created_at,
                s.data->'structured_metadata' as structured_metadata,
                s.source as source_engine
            FROM events e
            LEFT JOIN scraped_data s ON e.apply_link = s.url AND e.ig = s.ig
            WHERE e.ig = %s
            AND e.category = %s
        """
        params = [ig, category]
        
        if not include_sent:
            inner_query += " AND (e.zulip_sent = FALSE OR e.zulip_sent IS NULL)"
            
        inner_query, params = self._append_orchestrator_time_filter(inner_query, params, column="e.created_at")
        inner_query += """
            ORDER BY e.apply_link, e.validity_score DESC
        """
        # Wrap in subquery so we can ORDER BY score after DISTINCT ON
        query = f"""
            SELECT * FROM ({inner_query}) AS unique_events
            ORDER BY
                validity_score DESC,
                created_at DESC
            LIMIT %s;
        """
        params.append(limit)

        with db_conn.get_cursor(factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        # Adapt for agents (backward compatibility with scraped_data structure)
        for row in rows:
            row['_id'] = row['id']
            row['url'] = row['apply_link']
            row['data'] = {
                'location': row['location'] or 'Online',
                'platform': row['platform'] or 'RSS',
                'days_left': row['days_left'],
                'endDate': row['deadline'] or 'TBA',
                'summary': row['summary']
            }
            row['quality_score'] = row['validity_score'] or 0
            row['summary'] = row['summary'] or 'No summary available.'
            row['link'] = row['apply_link']
            row['source_engine'] = row.get('source_engine') or row['platform'] or ''
            row['category'] = row['category'] or 'Unknown'
            row['structured_metadata'] = row.get('structured_metadata') or {}
        return rows
        
    def update_event_summary_by_link(self, link: str, summary: str):
        """Update the events table summary for a given apply_link after Groq generates it."""
        if not link or not summary:
            return False
        with db_conn.get_cursor() as cur:
            cur.execute(
                """
                UPDATE events
                SET summary = %s, updated_at = %s
                WHERE apply_link = %s AND (summary IS NULL OR summary = '');
                """,
                (summary, datetime.now(timezone.utc), link),
            )
            return cur.rowcount > 0

    def close(self):
        # Postgres connection is persistent, ignore.
        pass

# MongoDB drop-in replacement naming
Database = DatabaseFacade

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
                doc_id = db_obj.insert_event(title, link, ig, "eventslink_parser", "not processed")
                if doc_id: inserted += 1
            else:
                modified += 1
    return inserted, modified

class _LazyDatabaseFacade:
    """Import-safe proxy that opens Postgres only when first used."""

    def __init__(self):
        self._instance = None

    def _get_instance(self):
        if self._instance is None:
            self._instance = DatabaseFacade()
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)


# Singleton-compatible proxy for existing scripts.
db = _LazyDatabaseFacade()

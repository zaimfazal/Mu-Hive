import json
from datetime import datetime, timezone
from psycopg2.extras import RealDictCursor
from src.db.connection import db_conn

class ScrapeRepository:
    """Handles all database operations related to raw scraped data."""

    @staticmethod
    def save(title, url, ig, status, data, scraped_at=None):
        """Saves raw scraped data to the database."""
        if scraped_at is None:
            scraped_at = datetime.now(timezone.utc)
            
        with db_conn.get_cursor() as cur:
            json_data = json.dumps(data) if data else None
            cur.execute("""
                INSERT INTO scraped_data (title, url, ig, status, scraped_at, data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (title, url, ig, status, scraped_at, json_data))
            return cur.fetchone()[0]

    @staticmethod
    def get_by_id(record_id):
        """Fetches a single scraped record by its ID."""
        with db_conn.get_cursor(factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM scraped_data WHERE id = %s;", (record_id,))
            return cur.fetchone()

    @staticmethod
    def get_all():
        """Fetches all scraped data records ordered by date."""
        with db_conn.get_cursor(factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM scraped_data ORDER BY scraped_at DESC;")
            return cur.fetchall()

    @staticmethod
    def link_exists(url, ig):
        with db_conn.get_cursor() as cur:
            cur.execute("SELECT id FROM scraped_data WHERE url = %s AND ig = %s", (url, ig))
            return cur.fetchone() is not None

    @staticmethod
    def insert_queue(title, url, ig, source_engine, status, data=None):
        if data is None: data = {}
        with db_conn.get_cursor() as cur:
            cur.execute("""
                INSERT INTO scraped_data (title, url, ig, source, status, data)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (url, ig) DO NOTHING
                RETURNING id;
            """, (title, url, ig, source_engine, status, json.dumps(data)))
            row = cur.fetchone()
            return row[0] if row else None

    @staticmethod
    def find_pending(limit=50):
        with db_conn.get_cursor(factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM scraped_data WHERE status = 'not processed' LIMIT %s;", (limit,))
            return cur.fetchall()

    @staticmethod
    def update_scrape(record_id, status, scrape_layer, scrape_data):
        with db_conn.get_cursor() as cur:
            cur.execute("SELECT data FROM scraped_data WHERE id = %s", (record_id,))
            row = cur.fetchone()
            current_data = row[0] if row and row[0] else {}
            current_data.update(scrape_data)
            
            cur.execute("""
                UPDATE scraped_data 
                SET status = %s, scrape_layer = %s, data = %s, scraped_at = %s
                WHERE id = %s
            """, (status, scrape_layer, json.dumps(current_data), datetime.now(timezone.utc), record_id))
            return cur.rowcount > 0

from src.db.connection import db_conn
from src.db.deliveries import ensure_delivery_schema

def initialize_schema():
    """Initializes the PostgreSQL database schema if tables don't exist."""
    print("[*] Initializing database schema...")
    try:
        with db_conn.get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scraped_data (
                    id SERIAL PRIMARY KEY,
                    title TEXT,
                    url TEXT NOT NULL,
                    ig TEXT,
                    source TEXT,
                    status TEXT,
                    scrape_layer TEXT,
                    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    data JSONB,
                    zulip_sent BOOLEAN DEFAULT FALSE
                );

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

                CREATE TABLE IF NOT EXISTS ig_mails (
                    id SERIAL PRIMARY KEY,
                    ig TEXT UNIQUE NOT NULL,
                    email TEXT NOT NULL
                );
            """)
            
            cur.execute("ALTER TABLE scraped_data ADD COLUMN IF NOT EXISTS zulip_sent BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS mail_sent BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS zulip_sent BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS deadline TEXT;")

            cur.execute("""
                DELETE FROM scraped_data a
                USING scraped_data b
                WHERE a.url = b.url
                  AND COALESCE(a.ig, '') = COALESCE(b.ig, '')
                  AND a.id < b.id;
            """)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'scraped_data_url_ig_key'
                    ) THEN
                        ALTER TABLE scraped_data
                        ADD CONSTRAINT scraped_data_url_ig_key UNIQUE (url, ig);
                    END IF;
                END $$;
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_scraped_data_status_scraped_at
                ON scraped_data (status, scraped_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_ig_category_score
                ON events (ig, category, validity_score DESC, created_at DESC);

                CREATE TABLE IF NOT EXISTS event_interest_groups (
                    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                    ig_name TEXT NOT NULL,
                    PRIMARY KEY (event_id, ig_name)
                );

                CREATE INDEX IF NOT EXISTS idx_event_igs_name
                ON event_interest_groups (ig_name);

                -- Backfill existing single-IG events into junction table
                INSERT INTO event_interest_groups (event_id, ig_name)
                SELECT id, ig FROM events
                WHERE ig IS NOT NULL AND ig != ''
                ON CONFLICT (event_id, ig_name) DO NOTHING;

                -- Backfill secondary IGs from validated multi-tag classifications
                -- stored on scraped_data. Repeatable: ON CONFLICT DO NOTHING.
                -- Rollback: TRUNCATE event_interest_groups, then re-run this
                -- initializer to rebuild from events.ig + validated tags.
                INSERT INTO event_interest_groups (event_id, ig_name)
                SELECT DISTINCT e.id, trim(jt.tag)
                FROM events e
                JOIN scraped_data s ON s.url = e.apply_link
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(s.data -> 'validated_tags') = 'array'
                         THEN s.data -> 'validated_tags'
                         WHEN jsonb_typeof(s.data -> 'ig_tags') = 'array'
                         THEN s.data -> 'ig_tags'
                         ELSE '[]'::jsonb END) AS jt(tag)
                WHERE jt.tag IS NOT NULL
                  AND trim(jt.tag) != ''
                  AND lower(trim(jt.tag)) != 'unknown'
                ON CONFLICT (event_id, ig_name) DO NOTHING;
            """)

            # Per-(event, IG, channel) delivery log (Phase 4A) + conservative
            # backfill from the legacy global sent flags.
            ensure_delivery_schema(cur)



        print("[+] Schema initialization complete.")
    except Exception as e:
        print(f"[!] Error initializing schema: {e}")



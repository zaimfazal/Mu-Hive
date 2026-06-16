from src.db.connection import db_conn

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
            """)



        print("[+] Schema initialization complete.")
    except Exception as e:
        print(f"[!] Error initializing schema: {e}")



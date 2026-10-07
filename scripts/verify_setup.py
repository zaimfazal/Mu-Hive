# scripts/verify_setup.py
"""
Verify that the Mu-Hive environment is correctly configured before running the pipeline.
Checks: Database connectivity (Postgres), Groq API key presence.
"""
import sys
import os
import psycopg2
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))


def test_db():
    """Verify PostgreSQL connection and schema."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE ERROR: DATABASE_URL not set in environment.")
        return False
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()

        # Check if scraped_data table exists
        cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'scraped_data');")
        scraped_data_exists = cursor.fetchone()[0]
        if not scraped_data_exists:
            print("[FAIL] DATABASE: 'scraped_data' table does not exist.")
            conn.close()
            return False

        # Check if events table exists
        cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'events');")
        events_exists = cursor.fetchone()[0]
        if not events_exists:
            print("[FAIL] DATABASE: 'events' table does not exist.")
            conn.close()
            return False

        # Check if zulip_sent column exists in scraped_data
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'scraped_data' AND column_name = 'zulip_sent'
            );
        """)
        zulip_sent_exists = cursor.fetchone()[0]
        if not zulip_sent_exists:
            print("[FAIL] DATABASE: 'zulip_sent' column does not exist in 'scraped_data'.")
            conn.close()
            return False

        # Placeholder mail mappings are written ONLY with explicit opt-in.
        # verify_setup.py must never write placeholder emails to a production
        # database by accident: without SEED_DEFAULT_IG_MAILS=true it only
        # validates and reports what is missing.
        # (AI, Cyber Security, Web Development) in the table ig_mails (default: test-email@gmail.com) if missing
        active_igs = ["AI", "Cyber Security", "Web Development"]
        seed_allowed = os.getenv("SEED_DEFAULT_IG_MAILS", "false").lower() == "true"

        # Check if ig_mails table exists first
        cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'ig_mails');")
        ig_mails_exists = cursor.fetchone()[0]
        if not ig_mails_exists:
            # Create if missing
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ig_mails (
                    id SERIAL PRIMARY KEY,
                    ig TEXT UNIQUE NOT NULL,
                    email TEXT NOT NULL
                );
            """)

        if not seed_allowed:
            cursor.execute("SELECT ig FROM ig_mails")
            existing_igs = {row[0] for row in cursor.fetchall()}
            missing_igs = [ig for ig in active_igs if ig not in existing_igs]
            if missing_igs:
                print(f"[WARN] DATABASE: ig_mails has no mapping for: {', '.join(missing_igs)}. "
                      f"Set SEED_DEFAULT_IG_MAILS=true to insert placeholder mappings, "
                      f"or insert real recipient addresses.")
            else:
                print("[OK]   DATABASE: ig_mails mappings present for active IGs.")
        else:
            for ig in active_igs:
                cursor.execute("SELECT 1 FROM ig_mails WHERE ig = %s", (ig,))
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO ig_mails (ig, email) VALUES (%s, %s) ON CONFLICT (ig) DO NOTHING",
                        (ig, "test-email@gmail.com")
                    )
                    print(f"[OK]   DATABASE: Inserted default mail mapping for IG: {ig}")

        # Counts
        cursor.execute("SELECT COUNT(*) FROM scraped_data")
        scraped_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM events")
        events_count = cursor.fetchone()[0]

        conn.close()
        print(f"[OK]   DATABASE: Postgres connected. {scraped_count} scraped, {events_count} events.")
        return True
    except Exception as e:
        print(f"[FAIL] DATABASE ERROR: {e}")
        return False


def test_groq():
    """Verify the Groq API key is set in environment."""
    if os.getenv("GROQ_API_KEY"):
        print("[OK]   GROQ: API key is configured.")
        return True
    else:
        print("[FAIL] GROQ: GROQ_API_KEY is not set! Add it to your .env file.")
        return False


if __name__ == '__main__':
    print("=" * 45)
    print("  Mu-Hive Environment Verification (Postgres)")
    print("=" * 45)

    db_ok = test_db()
    groq_ok = test_groq()

    print("-" * 45)
    if db_ok and groq_ok:
        print(">>> ALL CHECKS PASSED. Ready to run: python main.py")
        sys.exit(0)
    else:
        print(">>> SOME CHECKS FAILED. Fix the issues above before running.")
        sys.exit(1)

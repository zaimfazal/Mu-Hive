#!/usr/bin/env python3
import os
import sys
import argparse
import psycopg2
from dotenv import load_dotenv

# Find project root directory and load environment variables
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

def main():
    parser = argparse.ArgumentParser(description="Clear specific categories from the Mu-Hive database.")
    parser.add_argument(
        "--categories", 
        type=str, 
        default="Hackathons,News", 
        help="Comma-separated list of categories to clear (case-insensitive)."
    )
    parser.add_argument(
        "-y", "--yes", 
        action="store_true", 
        help="Bypass confirmation prompt."
    )
    args = parser.parse_args()

    # Parse and clean categories
    categories = [cat.strip().lower() for cat in args.categories.split(",") if cat.strip()]
    if not categories:
        print("[!] No categories specified to clear.")
        sys.exit(1)

    # Fetch database URL
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[!] DATABASE_URL not found in .env.")
        sys.exit(1)

    print(f"Connecting to database...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
    except Exception as e:
        print(f"[!] Connection failed: {e}")
        sys.exit(1)

    try:
        # 1. Count matching rows in scraped_data
        cur.execute(
            """
            SELECT COUNT(*) FROM scraped_data 
            WHERE LOWER(data->>'category') = ANY(%s);
            """,
            (categories,)
        )
        scraped_count = cur.fetchone()[0]

        # 2. Count matching rows in events
        cur.execute(
            """
            SELECT COUNT(*) FROM events 
            WHERE LOWER(category) = ANY(%s);
            """,
            (categories,)
        )
        events_count = cur.fetchone()[0]

        print("-" * 50)
        print(f"Target categories: {', '.join(categories)}")
        print(f"Matching rows found:")
        print(f"  - scraped_data: {scraped_count} rows")
        print(f"  - events: {events_count} rows")
        print("-" * 50)

        if scraped_count == 0 and events_count == 0:
            print("No matching records found. Nothing to clear.")
            return

        # 3. Prompt for confirmation
        if not args.yes:
            confirm = input(f"Are you sure you want to delete these records? (type 'yes' to confirm): ")
            if confirm.strip().lower() != 'yes':
                print("Operation cancelled.")
                return

        # 4. Perform deletions
        print("Executing deletions...")
        
        cur.execute(
            """
            DELETE FROM scraped_data 
            WHERE LOWER(data->>'category') = ANY(%s);
            """,
            (categories,)
        )
        scraped_deleted = cur.rowcount

        cur.execute(
            """
            DELETE FROM events 
            WHERE LOWER(category) = ANY(%s);
            """,
            (categories,)
        )
        events_deleted = cur.rowcount

        print("[+] Deletion completed successfully:")
        print(f"  - Deleted {scraped_deleted} rows from scraped_data")
        print(f"  - Deleted {events_deleted} rows from events")

    except Exception as e:
        print(f"[!] Error during database operation: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
